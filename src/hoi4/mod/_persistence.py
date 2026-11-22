"""Rendering pending edits to files, diffs, atomic save, discard and transactions."""

from __future__ import annotations

import dataclasses
import os
import tempfile
import warnings
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager, suppress
from typing import Self
from pathlib import Path

from ..bookmarks import (
    patch_bookmark_dates_defines,
    serialize_bookmarks_file,
)
from ..characters import (
    serialize_characters_file,
)
from ..countries import (
    country_color_tags,
    country_file_paths,
    seed_country_colors_file,
    serialize_country_colors_file,
    serialize_country_files,
)
from ..decisions import (
    serialize_decision_categories_file,
    serialize_decisions_file,
)
from ..diff import unified_diff
from ..errors import SaveRefusedError
from ..dynamic_modifiers import (
    serialize_dynamic_modifiers_file,
)
from ..events import serialize_events_file
from ..focus import serialize_focus_file
from ..ideas import serialize_ideas_file
from ..ideologies import load_ideologies_file, serialize_ideologies_file
from ..layers import FolderView
from ..localisation import (
    DEFAULT_LOCALISATION_LANGUAGE,
    localisation_language_for_path,
    serialize_localization_file,
)
from ..on_actions import serialize_on_actions_file
from ..oob import (
    serialize_oob,
)
from ..paths import (
    resolve_mod_output_path,
)
from ..states import (
    serialize_state,
)
from ..syntax import parse, repair
from ..tags import (
    country_tag_entries,
    load_vanilla_tags,
    remove_country_tag_entries,
    sort_generated_country_tags,
)
from ..textio import encode_text, read_text
from ..types import (
    SaveResult,
)
from ._core import (
    STAGED_FILE_DIRTY_NAMES,
    CoreLayer,
    _changed_files,
)
from ._loading import LoadingLayer
from ._preview import PreviewLayer
from ._shared import (
    ExternalModificationError,
)
from ._sources import SourcesLayer


# Files that hold Paradox script and are checked for syntax before a save.
_SCRIPT_SUFFIXES = frozenset({".txt", ".gfx", ".gui", ".asset"})


class PersistenceLayer(PreviewLayer, LoadingLayer, SourcesLayer):
    """Rendering pending edits to files, diffs, atomic save, discard and transactions."""

    # ── Preview & Save ───────────────────────────────────────────

    def preview_summary(self) -> str:
        """Describe the unsaved edits in a few lines, one per changed object or file.

        Returns ``"No semantic changes detected"`` when there are none.
        """
        lines = self._semantic_preview_lines()
        lines.extend(f"support script: {p.relative_to(self.mod_root)}" for p in self._script_file_updates)
        if not lines:
            return "No semantic changes detected"
        return "Changed:\n" + "\n".join(f"- {line}" for line in lines)

    def preview(self) -> str:
        """Return a unified diff of every file ``save()`` would write; writes nothing.

        Binary assets get one line each instead of a diff. An empty string
        means ``save()`` would change no file.

        Raises:
            ExternalModificationError: A file it would rewrite changed on disk
                after the mod read it; call ``discard()`` and redo the edit.
        """
        diffs: list[str] = []
        rendered = self._render_dirty_files()
        self._assert_no_external_modifications(rendered)
        # In path order, so the same edits always give the same text.
        for path, current in sorted(rendered.items(), key=lambda item: item[0]):
            original = self._read_current_text(path)
            rel = str(path.relative_to(self.mod_root))
            if current is None:
                current = ""
            diff = unified_diff(original, current, rel)
            if diff:
                diffs.append(diff)
        for path, content in sorted(self._pending_asset_writes.items()):
            baseline = self._pending_asset_baselines[path]
            if baseline == content:
                continue
            action = "create" if baseline is None else "replace"
            rel = path.relative_to(self.mod_root).as_posix()
            diffs.append(f"Binary asset {action}: {rel} ({len(content)} bytes)")
        return "\n".join(diffs)

    def save(self, *, require_changes: bool = False) -> SaveResult:
        """Write every staged edit to disk at once.

        Either every file is written or, on an error, none is (files already
        replaced are put back). The written sections load again from disk on
        next use. With nothing staged, a ``RuntimeWarning`` is issued.

        Args:
            require_changes: Raise instead of returning when no file changes.

        Returns:
            The written files and the sections that had edits.

        Raises:
            ExternalModificationError: A file it would rewrite changed on disk
                after the mod read it; call ``discard()`` and redo the edit.
            SaveRefusedError: ``require_changes`` is true and no file
                changes, or writing would lose or break content (a file it
                would rewrite failed to load, a new syntax problem). It is
                also a ``RuntimeError``.
            OSError: Writing a file failed.
        """
        if not self._dirty:
            self._drop_inherited_lookups()
            message = "No changes written: save() was called with no dirty changes"
            warnings.warn(message, RuntimeWarning, stacklevel=2)
            if require_changes:
                raise SaveRefusedError(message)
            return SaveResult(written_files=[], dirty_sections=[], no_changes=True, message=message)

        dirty_sections = sorted(self._dirty)
        rendered = self._render_dirty_files()
        self._assert_no_external_modifications(rendered)
        changed = {
            path: content
            for path, content in rendered.items()
            if (content is None and path.exists())
            or (content is not None and (not path.exists() or self._read_current_text(path) != content))
        }
        changed_assets = {
            path: content
            for path, content in self._pending_asset_writes.items()
            if self._pending_asset_baselines[path] != content
        }
        if not changed and not changed_assets:
            self._drop_inherited_lookups()
            message = "No files written after processing dirty sections"
            if require_changes:
                raise SaveRefusedError(message)
            return SaveResult(dirty_sections=dirty_sections, no_changes=True, message=message)
        self._commit_rendered_files(changed, changed_assets)
        written_files = sorted((*changed, *changed_assets))
        self._note_written(written_files)
        self._discard_changes(written_files)
        return SaveResult(
            written_files=written_files,
            dirty_sections=dirty_sections,
            no_changes=False,
            message=f"Saved {len(written_files)} file(s)",
        )

    def _render_dirty_files(self) -> dict[Path, str | None]:
        rendered: dict[Path, str | None] = dict(self._script_file_updates)
        if "country_names" in self._dirty:
            path = self.mod_root / "common" / "names" / "00_generated_names.txt"
            text = self._render_country_name_pools()
            rendered[path] = text or None
        if "countries" in self._dirty:
            tag_path = self.mod_root / "common" / "country_tags" / "00_generated_tags.txt"
            tag_text = remove_country_tag_entries(
                self._read_current_text(tag_path), set(self._deleted_countries)
            )
            for tag in sorted(self._dirty_countries):
                if tag not in self._countries:
                    continue
                line = f'{tag} = "countries/{tag}.txt"'
                declared_in_mod = tag in self._country_tag_mappings.get(FolderView(self.mod_root), {})
                declared_by_vanilla = tag in self._vanilla_tags
                if not declared_in_mod and not declared_by_vanilla and tag not in {
                    registered for registered, _ in country_tag_entries(tag_text)
                }:
                    tag_text = tag_text.rstrip() + ("\n" if tag_text.strip() else "") + line + "\n"
            tag_text = sort_generated_country_tags(tag_text)
            rendered[tag_path] = tag_text or None
            tags_dir = self.mod_root / "common" / "country_tags"
            if self._deleted_countries and tags_dir.is_dir():
                for source_path in sorted(tags_dir.glob("*.txt")):
                    if source_path == tag_path:
                        continue
                    source_text = self._read_current_text(source_path)
                    updated_text = remove_country_tag_entries(
                        source_text, set(self._deleted_countries)
                    )
                    if updated_text != source_text:
                        rendered[source_path] = updated_text or None
            for deleted_country in self._deleted_countries.values():
                for path in country_file_paths(self.mod_root, deleted_country):
                    rendered[resolve_mod_output_path(self.mod_root, path)] = None
            for tag in sorted(self._dirty_countries):
                dirty_country = self._countries.get(tag)
                if dirty_country is None:
                    continue
                country_files = serialize_country_files(
                    self.mod_root,
                    dirty_country,
                    keep_history_path=self._overrides_inherited(dirty_country.history_path),
                )
                for path, content in country_files.items():
                    if path in self._dirty_character_files:
                        continue
                    if not path.exists() and self._same_as_inherited(path, content):
                        # A vanilla-backed country edit retargets all of its
                        # files; one the edit did not change stays inherited
                        # instead of becoming a stale copy in the mod.
                        continue
                    rendered[path] = content
                if (
                    dirty_country.history_path is not None
                    and dirty_country.history_path not in country_files
                ):
                    rendered[dirty_country.history_path] = None
            colors_path = self.mod_root / "common" / "countries" / "colors.txt"
            colors_original = self._read_current_text(colors_path)
            existing_color_tags = country_color_tags(colors_original)
            color_updates = {
                tag: country.color
                for tag in self._dirty_countries
                if (country := self._countries.get(tag)) is not None
                and {"*", "color", "color_ui"} & country.touched_fields
                and (tag in self._vanilla_tags or tag in existing_color_tags)
            }
            ui_color_updates = {
                tag: self._countries[tag].color_ui for tag in color_updates
            }
            deleted_color_tags = set(self._deleted_countries) & existing_color_tags
            colors_base = self._script_file_updates.get(colors_path, colors_original)
            if (color_updates or deleted_color_tags) and self._base_content is not None and colors_path not in self._script_file_updates:
                vanilla_colors_path = self._base_content.file("common/countries/colors.txt")
                if vanilla_colors_path is not None:
                    colors_base = seed_country_colors_file(
                        colors_base,
                        read_text(vanilla_colors_path),
                    )
                elif set(color_updates) & self._vanilla_tags:
                    raise SaveRefusedError(
                        "Cannot safely override a vanilla country color because "
                        f"{vanilla_colors_path} is missing. HOI4 replaces colors.txt "
                        "wholesale, so writing a partial table would remove other "
                        "countries' map colors."
                    )
            colors_text = serialize_country_colors_file(
                colors_base,
                color_updates,
                deleted_tags=deleted_color_tags,
                ui_colors=ui_color_updates,
            )
            if colors_text != colors_original:
                rendered[colors_path] = colors_text or None

        if "characters" in self._dirty:
            for path, characters in self._group_characters_by_file(
                dirty_only=True
            ).items():
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"character"}),
                )
                rendered[path] = serialize_characters_file(
                    characters,
                    self._original_files.get(path, ""),
                )

        if "oobs" in self._dirty:
            for path in self._dirty_oob_files:
                matching = next(
                    (
                        oob
                        for oob in self._oobs.values()
                        if oob.path == path
                    ),
                    None,
                )
                rendered[path] = serialize_oob(matching) if matching is not None else None

        if "states" in self._dirty:
            rendered.update(self._deleted_state_files)
            for state_id in self._dirty_states:
                state = self._states.get(state_id)
                if state is None:
                    continue
                path = resolve_mod_output_path(
                    self.mod_root,
                    state.path or Path("history") / "states" / f"{state_id}-STATE.txt",
                )
                rendered[path] = serialize_state(state)

        if "map" in self._dirty:
            for relative, map_text in self._map_file_updates.items():
                rendered[self.mod_root / relative] = map_text

        if "events" in self._dirty:
            event_files, namespaces = self._group_events_by_file(dirty_only=True)
            for path, events in event_files.items():
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"event"}),
                )
                rendered[path] = serialize_events_file(
                    namespaces.get(path), events, self._original_files.get(path, "")
                )

        if "on_actions" in self._dirty:
            for path, actions in self._group_on_actions_by_file(dirty_only=True).items():
                original = self._original_files.get(path)
                if not actions and original is None:
                    # A generated occurrence created and deleted before its
                    # first save should not leave an empty file behind.
                    rendered[path] = None
                else:
                    # Existing files may contain comments, scalar metadata, or
                    # intentionally empty hooks.  Remove only the selected
                    # modeled occurrence and retain the file itself.
                    rendered[path] = serialize_on_actions_file(
                        actions, original or ""
                    )

        if "decisions" in self._dirty:
            for path, categories in self._group_decisions_by_file(dirty_only=True).items():
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"decision", "decision_category"}),
                )
                rendered[path] = serialize_decisions_file(
                    categories, self._original_files.get(path, "")
                )
            for path, categories in self._group_decision_categories_by_file(
                dirty_only=True
            ).items():
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"decision_category"}),
                )
                category_text = serialize_decision_categories_file(
                    categories, self._original_files.get(path, "")
                )
                rendered[path] = category_text if category_text.strip() else None

        if "ideas" in self._dirty:
            for path, ideas in self._group_ideas_by_file(dirty_only=True).items():
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"idea"}),
                )
                container = self._idea_file_containers.get(path)
                if container is None:
                    container = "ideas" if path.parent.name == "ideas" else "country_ideas"
                rendered[path] = serialize_ideas_file(
                    ideas,
                    container_name=container,
                    original=self._original_files.get(path, ""),
                )

        if "ideologies" in self._dirty:
            for path in self._dirty_ideology_files:
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"ideology"}),
                )
                ideologies = [
                    ideology
                    for ideology in self._ideologies.values()
                    if ideology.path == path
                ]
                original = self._original_files.get(path, "")
                if (
                    path not in self._original_files
                    and not path.is_file()
                    and self._base_content is not None
                ):
                    # A same-path override shadows the entire inherited file.
                    # Seed both text and models: the serializer removes definitions
                    # missing from its list. The fallback applies layer precedence
                    # and replace_path; even an empty writable file wins over it.
                    source = self._base_content.file(path.relative_to(self.mod_root))
                    if source is not None:
                        original = self._read_current_text(source)
                        updated_ids = {ideology.id for ideology in ideologies}
                        ideologies.extend(
                            sibling for sibling in load_ideologies_file(source)
                            if sibling.id not in updated_ids
                        )
                rendered[path] = serialize_ideologies_file(
                    ideologies,
                    original=original,
                )

        if "dynamic_modifiers" in self._dirty:
            for path in self._dirty_dynamic_modifier_files:
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"dynamic_modifier"}),
                )
                modifiers = [
                    modifier
                    for modifier in self._dynamic_modifiers.values()
                    if modifier.path == path
                ]
                dynamic_text = serialize_dynamic_modifiers_file(
                    modifiers,
                    original=self._original_files.get(path, ""),
                )
                inherited = (
                    self._base_content is not None
                    and self._base_content.file(path.relative_to(self.mod_root)) is not None
                )
                rendered[path] = dynamic_text if dynamic_text.strip() or inherited else None

        if "bookmarks" in self._dirty:
            for path in self._dirty_bookmark_files:
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"bookmark"}),
                )
                bookmarks = [bookmark for bookmark in self._bookmarks if bookmark.path == path]
                rendered[path] = serialize_bookmarks_file(
                    bookmarks,
                    original=self._original_files.get(path, ""),
                )

        if "bookmark_dates" in self._dirty and self._bookmark_date_defines is not None:
            path, start_date, end_date = self._bookmark_date_defines
            rendered[path] = patch_bookmark_dates_defines(
                self._original_files.get(path, ""),
                start_date=start_date,
                end_date=end_date,
            )

        if "focus" in self._dirty:
            for path, trees in self._group_focus_trees_by_file(dirty_only=True).items():
                self._assert_no_unmodeled_duplicates_in_file(
                    path,
                    sections=frozenset({"focus_tree", "focus"}),
                )
                rendered[path] = serialize_focus_file(trees, self._original_files.get(path, ""))

        if "localization" in self._dirty:
            for path, entries in self._group_loc_by_file(dirty_only=True).items():
                rendered[path] = (
                    serialize_localization_file(
                        entries,
                        original=self._original_files.get(path, ""),
                        language=localisation_language_for_path(
                            path.relative_to(self.mod_root)
                        )
                        or DEFAULT_LOCALISATION_LANGUAGE,
                    )
                    if entries or (
                        self._content_layers is not None
                        and self._content_layers.inherited(path.relative_to(self.mod_root))
                        is not None
                    )
                    else None
                )

        resolved = {
            resolve_mod_output_path(self.mod_root, path): content
            for path, content in rendered.items()
        }
        for output_path, output in resolved.items():
            if output is not None:
                # Here, not at write time, so preview() shows what save() writes.
                resolved[output_path] = self._repair_new_override(output_path, output)
        self._refuse_failed_loads(resolved)
        self._refuse_new_syntax_problems(resolved)
        return resolved

    def _assert_no_external_modifications(
        self, rendered: dict[Path, str | None]
    ) -> None:
        conflicts = [
            path.resolve(strict=False)
            for path in rendered
            if self._changed_behind_our_back(path)
        ]
        # discard() finds changed files by size and time. These were found by
        # their text, so it is told: after a refused save, discard() reads
        # them again whatever their size and time say.
        self._seen_changed.update(conflicts)
        for path, baseline in self._pending_asset_baselines.items():
            current = path.read_bytes() if path.is_file() else None
            if current != baseline:
                conflicts.append(path)
        if conflicts:
            displayed = ", ".join(
                str(path.relative_to(self.mod_root)) for path in sorted(conflicts)
            )
            raise ExternalModificationError(
                "Refusing to overwrite files changed after Mod loaded them: "
                f"{displayed}. Call discard() and reapply the intended edit."
            )

    def _same_as_inherited(self, path: Path, content: str) -> bool:
        """Return whether ``content`` only repeats the file ``path`` would shadow."""

        try:
            shadowed = self.resolve_content_path(path.relative_to(self.mod_root))
        except ValueError:
            return False
        if shadowed is None or not shadowed.is_file() or shadowed == path:
            return False
        inherited = read_text(shadowed)
        return content == inherited or content == repair(inherited)

    def _repair_new_override(self, path: Path, content: str) -> str:
        """Close the braces of a new mod file that copies a tolerated inherited one.

        The game reads its own files with missing or stray braces; the mod's
        copy is written out well formed.
        """

        if path.exists():
            return content
        try:
            shadowed = self.resolve_content_path(path.relative_to(self.mod_root))
        except ValueError:
            return content
        if shadowed is None or not shadowed.is_file() or shadowed == path:
            return content
        if parse(read_text(shadowed)).problems:
            return repair(content)
        return content

    def _refuse_new_syntax_problems(self, rendered: dict[Path, str | None]) -> None:
        """Refuse to write a script file with a syntax problem it did not have before.

        This is the last check before anything reaches the disk: whatever went
        wrong on the way (malformed script passed to the SDK, or a bug in a
        patcher), a file is never left more broken than it was.
        """

        for path, content in rendered.items():
            if content is None or path.suffix.lower() not in _SCRIPT_SUFFIXES:
                continue
            document = parse(content)
            if not document.problems:
                continue
            if path.is_file():
                before = len(parse(self._read_current_text(path)).problems)
            else:
                try:
                    shadowed = self.resolve_content_path(path.relative_to(self.mod_root))
                except ValueError:
                    shadowed = None
                before = (
                    len(parse(repair(read_text(shadowed))).problems)
                    if shadowed is not None and shadowed.is_file() and shadowed != path
                    else 0
                )
            if len(document.problems) <= before:
                continue
            problem = document.problems[0]
            line, column = document.line_col(problem.offset)
            raise SaveRefusedError(
                f"Refusing to write {path}: it would get a syntax problem it does not have "
                f"now ({problem.message}, line {line}, column {column}). Check the script "
                "text passed to the last edits; nothing was written."
            )

    def _refuse_failed_loads(self, rendered: dict[Path, str | None]) -> None:
        """Refuse to write over, or next to, a mod file that could not be loaded.

        Its content was never fully modeled, so writing it from the model
        would silently drop what the author wrote. And while it is unreadable,
        the SDK edits the inherited version of whatever it defines: writing
        that as a new override file would give the mod the same content twice.
        """

        # Only sections that own a folder written to can have a failed file there.
        self._load_sections_for_paths(rendered)
        failed = {
            diagnostic.path.resolve(strict=False): diagnostic
            for diagnostic in self._loaded_diagnostics()
            if diagnostic.error_type != "DuplicateIdentifierError"
        }
        if not failed:
            return
        failed_folders = {path.parent: diagnostic for path, diagnostic in failed.items()}
        for path, content in rendered.items():
            resolved = path.resolve(strict=False)
            neighbour = failed_folders.get(resolved.parent)
            if content is not None and neighbour is not None and self._shadows_inherited(path):
                raise SaveRefusedError(
                    f"Refusing to create {path}: it would override an inherited file, but "
                    f"{neighbour.path} in the same folder failed to load "
                    f"({neighbour.error_type}: {neighbour.message}) and may already hold the "
                    "mod's version of this content. Fix that file and call discard() first."
                )
            diagnostic = failed.get(resolved)
            if diagnostic is None or not path.is_file():
                continue
            if content is not None and content == self._read_current_text(path):
                continue
            raise SaveRefusedError(
                f"Refusing to rewrite {path}: it failed to load "
                f"({diagnostic.error_type}: {diagnostic.message}). Fix the file "
                "and call discard() before editing it."
            )

    def _encode_like_source(self, path: Path, content: str, current: bytes | None) -> bytes:
        """Encode ``content`` for ``path`` the way its source file is stored.

        An existing file keeps its encoding, byte-order mark and line endings,
        and every line that did not change keeps its exact bytes. A new file
        that overrides an inherited one copies those properties from the file
        it shadows. Localisation always gets the byte-order mark the game
        requires.
        """

        bom = True if path.suffix.lower() == ".yml" else None
        donor = self._format_donors.get(path)
        if donor is not None:
            return encode_text(content, like=donor, bom=bom)
        if current is not None:
            return encode_text(content, like=current, bom=bom)
        try:
            shadowed = self.resolve_content_path(path.relative_to(self.mod_root))
        except ValueError:
            shadowed = None
        if shadowed is not None and shadowed.is_file() and shadowed != path:
            donor = shadowed.read_bytes()
        return encode_text(content, like=donor, bom=bom)

    def _commit_rendered_files(
        self,
        rendered: dict[Path, str | None],
        assets: dict[Path, bytes] | None = None,
    ) -> None:
        assets = assets or {}
        overlap = set(rendered) & set(assets)
        if overlap:
            raise RuntimeError(
                "A file cannot be staged as both text and binary: "
                + ", ".join(str(path) for path in sorted(overlap))
            )
        targets = set(rendered) | set(assets)
        backups = {path: path.read_bytes() if path.exists() else None for path in targets}
        prepared: dict[Path, Path] = {}
        try:
            for path, content in rendered.items():
                if content is None:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
                    handle.write(self._encode_like_source(path, content, backups[path]))
                    prepared[path] = Path(handle.name)
            for path, binary_content in assets.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
                    handle.write(binary_content)
                    prepared[path] = Path(handle.name)
            for path, text_content in rendered.items():
                if text_content is None:
                    path.unlink(missing_ok=True)
                else:
                    temporary = prepared[path]
                    os.replace(temporary, path)
                    del prepared[path]
            for path in assets:
                temporary = prepared[path]
                os.replace(temporary, path)
                del prepared[path]
        except Exception:
            for temporary in prepared.values():
                temporary.unlink(missing_ok=True)
            for path, backup in backups.items():
                if backup is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(backup)
            raise

    @contextmanager
    def transaction(self, *, save: bool = False) -> Iterator[Self]:
        """Temporarily mutate the mod, then save or restore in-memory state.

        Use ``with mod.transaction():`` for dry runs that call ``preview()`` inside
        the block. On normal exit the transaction is discarded unless
        ``save=True`` is passed. On exceptions, in-memory state is restored.

        Yields:
            The mod itself.

        Raises:
            Exception: Whatever the block raises, after the state is restored;
                with ``save=True``, also what ``save()`` raises.
        """
        snapshot = self._snapshot()
        # A rollback puts back everything an edit inside it changed, so an
        # edit that failed inside it is undone with the rest.
        edit_failed = self._edit_failed
        try:
            yield self
            if save:
                self.save()
            else:
                self._restore(snapshot)
                self._edit_failed = edit_failed
        except Exception:
            self._restore(snapshot)
            self._edit_failed = edit_failed
            raise

    def discard(self) -> None:
        """Drop every unsaved edit and pick up files changed on disk.

        This is the cheap way back to the files: only sections that had
        edits or whose files changed on disk (and the sections built from
        them) are unloaded; they load again on next use. Everything else
        stays in memory. When a base mod's text file changed, everything is
        unloaded and the content layers are rebuilt.

        It looks only at the files of the mod and its base mods. A file
        counts as changed when its size or modification time and its text
        differ, or when a refused ``save()`` saw its text change. Use
        ``reload()`` instead when a game file changed, or when a file may
        have changed without its size or time showing it.
        """

        self._discard_changes(())

    def reload(self) -> None:
        """Drop every unsaved edit and read the whole mod again from disk.

        Unlike ``discard()``, this does not trust file sizes and times: every
        section of the mod, the base mods and the game is read again on next
        use. It is slower than ``discard()``. Use it when files changed in a
        way ``discard()`` cannot see (a game file, a file written back with
        its old size and time), or to start from a clean state.
        """

        self._reload_all()

    def _reload_all(self) -> None:
        """Discard queued changes and reload every section from disk."""

        self._refresh_content_layers()
        self._vanilla_tags = (
            load_vanilla_tags(self._base_content) if self._base_content is not None else set()
        )
        self._vanilla_state_loc_entries = None
        self._reset_pending_edits()
        self._unload_sections([section.name for section in self._sections.sections])
        self._original_files.clear()
        self._clear_derived_caches()
        self._capture_source_baseline()
        self._after_reload()

    def _discard_changes(self, written: Sequence[Path]) -> None:
        """Drop unsaved edits; unload what they touched and what changed on disk.

        ``written`` are the files a save has just written or deleted. Sections
        with unsaved edits, sections whose files were written or changed on
        disk (found by fingerprint, without reading files), and the sections
        built from those are unloaded; the rest stays loaded and matches the
        files on disk.
        """

        if self._edit_failed:
            # An edit call raised after it may have changed loaded data,
            # without a dirty mark to find it by. Read everything again, as
            # every save() and discard() used to.
            self.reload()
            return
        staged = [*self._script_file_updates, *self._pending_asset_writes]
        fingerprints, digests = self._scan_fingerprints()
        known_digests = {**self._scan_digests, **self._read_digests}
        changed = _changed_files(self._fingerprints, known_digests, fingerprints, digests)
        # Plus what a refused save or preview saw changed by its text.
        changed = sorted({*changed, *self._seen_changed})
        names, mapped = self._sections_to_unload([*written, *staged, *changed])
        if not mapped or self._base_mods_changed():
            # A descriptor, a path outside the mod or a changed base mod
            # changes what every section sees, including the inherited layers.
            self._reload_all()
            return
        self._reset_pending_edits()
        dropped = {*written, *staged, *changed}
        self._unload_sections(names, dropped_files=dropped)
        self._clear_derived_caches(names, dropped)
        self._fingerprints = fingerprints
        for path in changed:
            self._read_digests.pop(path, None)
        self._seen_changed = set()
        self._scan_digests = {
            path: digest for path, digest in digests.items() if path not in self._read_digests
        }
        self._after_reload()

    def _sections_to_unload(self, paths: Sequence[Path]) -> tuple[set[str], bool]:
        """The sections to unload, and False when a path cannot be mapped."""

        registry = self._sections
        every = {section.name for section in registry.sections}
        names: set[str] = set(self._reload_on_discard)
        for dirty_name in self._dirty:
            if dirty_name in STAGED_FILE_DIRTY_NAMES:
                continue  # mapped through the staged paths below
            sections = registry.by_dirty_name.get(dirty_name)
            if sections is None:
                return every, True
            names |= sections
        for path in paths:
            readers = self._sections_reading(path)
            if readers is None:
                return every, False
            names |= readers
        return registry.with_dependents(names), True

    def _clear_derived_caches(
        self, unloaded: Collection[str] | None = None, dropped: Collection[Path] = ()
    ) -> None:
        """Clear what is built from files or loaded data and rebuilt on demand.

        ``None`` clears everything (a full reload). After a partial reload,
        ``unloaded`` names the sections that were unloaded and ``dropped``
        the files that were written, staged or changed on disk. Caches keyed
        by a file's text check that text on every use and stay valid, so
        they keep the entries of files nothing changed: the references of
        the mod's own files outside the unloaded sections, and the parsed
        sprite and runtime-activation texts. Everything else is cleared;
        it is rebuilt from per-file results that ``hoi4._file_cache`` keeps,
        so that is cheap.
        """

        self._scan_cache.clear()
        # What lookups read from the game and the base mods: a written or
        # changed mod file may now hide an inherited one.
        self._drop_inherited_lookups()
        if unloaded is None:
            self._own_reference_cache.clear()
            self._sprite_text_parse_cache.clear()
            self._runtime_activation_parse_cache.clear()
            self._script_trigger_index_cache.clear()
        else:
            gone = set(unloaded)
            for path in list(self._own_reference_cache):
                if path in dropped or (self._sections_reading(path) or set()) & gone:
                    del self._own_reference_cache[path]
        self._equipment_unlock_cache = None
        self._sprite_texture_cache = None
        self._state_index_cache.clear()
        self._country_context_cache.clear()

    def _after_reload(self) -> None:
        if self.strict_loading:
            # A broken file still fails at save() or discard(), as before
            # sections loaded lazily.
            self._load_all_sections()
        if os.environ.get("HOI4_VERIFY_RELOAD") == "1":
            self._verify_reload()

    def _verify_reload(self) -> None:
        """Check the partial reload against a full one (``HOI4_VERIFY_RELOAD=1``).

        A fresh ``Mod`` is built on the same folders and every section of
        both is compared by value. Sections this check loads are unloaded
        again afterwards, so the check does not change what is loaded.
        """

        fresh = object.__new__(type(self))
        CoreLayer.__init__(fresh, self.mod_root, self.hoi4_install)
        # Share the inherited layers: a partial reload keeps them by design.
        fresh.base_mod_paths = self.base_mod_paths
        fresh._content_layers = self._content_layers
        fresh._base_content = self._base_content
        fresh._vanilla_tags = set(self._vanilla_tags)
        before = set(self._section_loaded)
        # The check loads every section of this Mod, and every loader notes
        # the files it reads. Without the check those files are first read
        # later, so what is noted here is put back afterwards: the check
        # must not change which read counts as the first.
        noted = (dict(self._fingerprints), dict(self._read_digests), dict(self._scan_digests))
        try:
            self._load_all_sections()
            fresh._load_all_sections()
            for section in self._sections.sections:
                for attribute, lookup in section.caches:
                    self._fill_cache_for_check(fresh, attribute, lookup)
            for section in self._sections.sections:
                for attribute, _ in section.data:
                    live_value = self.__dict__[attribute]
                    fresh_value = fresh.__dict__[attribute]
                    if attribute == "_inherited_state_source_paths" and (
                        live_value is None or fresh_value is None
                    ):
                        continue  # built on demand by a lookup
                    difference = _first_difference(live_value, fresh_value, self.mod_root)
                    if difference is not None:
                        raise AssertionError(
                            f"Partial reload differs from a full reload in section "
                            f"{section.name!r}, {attribute}: first difference at {difference}"
                        )
                difference = _first_difference(
                    self._section_diagnostics.get(section.name, []),
                    fresh._section_diagnostics.get(section.name, []),
                    self.mod_root,
                )
                if difference is not None:
                    raise AssertionError(
                        f"Partial reload differs from a full reload in section "
                        f"{section.name!r}, load diagnostics: first difference at {difference}"
                    )
            # Lookups read game and base-mod files on demand; those layers are shared.
            live_originals = {
                path: text for path, text in self._original_files.items()
                if path in fresh._original_files or path.is_relative_to(self.mod_root)
            }
            difference = _first_difference(live_originals, fresh._original_files, self.mod_root)
            if difference is not None:
                raise AssertionError(
                    f"Partial reload differs from a full reload in the original file texts: "
                    f"first difference at {difference}"
                )
        finally:
            self._unload_sections(self._section_loaded - before)
            self._fingerprints, self._read_digests, self._scan_digests = noted

    def _fill_cache_for_check(self, fresh: PersistenceLayer, attribute: str, lookup: str) -> None:
        """Let ``fresh`` look up what lookups added to a cache of this mod.

        Only entries of the mod's own files: a lookup that cached content of
        the game or a base mod marks its section (``_reload_on_discard``), so
        none should be left. One that is shows up as a difference.
        """

        live = self.__dict__[attribute]
        known = fresh.__dict__[attribute]
        for key in list(live):
            if key in known:
                continue
            value = live[key] if isinstance(live, dict) else None
            if value is not None and not _comes_from(value, self.mod_root):
                continue
            with suppress(KeyError):
                getattr(fresh, lookup)(key)


def _comes_from(value: object, root: Path) -> bool:
    """Whether a model (or a path) was read from a file below ``root``.

    A value without any path counts as coming from ``root``.
    """

    if isinstance(value, Path):
        return value.is_relative_to(root)
    paths = [
        path
        for name in ("path", "source_path", "definition_path", "history_path", "character_path")
        if isinstance(path := getattr(value, name, None), Path)
    ]
    return not paths or any(path.is_relative_to(root) for path in paths)


def _first_difference(live: object, fresh: object, root: Path, where: str = "") -> str | None:
    """Where two loaded values first differ, or None when they are equal.

    Models are compared field by field, so the message names the first id
    and field that differ.
    """

    seen: set[tuple[int, int]] = set()

    def walk(left: object, right: object, where: str) -> str | None:
        if left is right:
            return None
        if type(left) is not type(right):
            return f"{where or 'value'}: {left!r} != {right!r}"
        if isinstance(left, (str, bytes, int, float, bool, Path, type(None))):
            return None if left == right else f"{where or 'value'}: {left!r} != {right!r}"
        pair = (id(left), id(right))
        if pair in seen:
            return None
        seen.add(pair)
        if isinstance(left, dict):
            assert isinstance(right, dict)
            for key in left:
                if key not in right:
                    return f"{where}[{key!r}]: only in the partially reloaded mod"
            for key in right:
                if key not in left:
                    return f"{where}[{key!r}]: only in the fully reloaded mod"
            for key in left:
                found = walk(left[key], right[key], f"{where}[{key!r}]")
                if found is not None:
                    return found
            return None
        if isinstance(left, (list, tuple)):
            assert isinstance(right, (list, tuple))
            for index, (a, b) in enumerate(zip(left, right, strict=False)):
                found = walk(a, b, f"{where}[{index}]")
                if found is not None:
                    return found
            if len(left) != len(right):
                return f"{where}: {len(left)} items != {len(right)} items"
            return None
        if isinstance(left, (set, frozenset)):
            assert isinstance(right, (set, frozenset))
            if left != right:
                extra = sorted(map(repr, left ^ right))[:3]
                return f"{where}: sets differ in {', '.join(extra)}"
            return None
        if dataclasses.is_dataclass(left) and not isinstance(left, type):
            for field in dataclasses.fields(left):
                found = walk(getattr(left, field.name), getattr(right, field.name), f"{where}.{field.name}")
                if found is not None:
                    return found
            return None
        if hasattr(left, "__dict__"):
            return walk(vars(left), vars(right), where)
        return None if left == right else f"{where or 'value'}: {left!r} != {right!r}"

    return walk(live, fresh, where)
