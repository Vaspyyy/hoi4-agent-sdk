"""Rendering pending edits to files, diffs, atomic save, discard and transactions."""

from __future__ import annotations

import os
import tempfile
import warnings
from contextlib import contextmanager
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
from ..dynamic_modifiers import (
    serialize_dynamic_modifiers_file,
)
from ..events import serialize_events_file
from ..focus import serialize_focus_file
from ..ideas import serialize_ideas_file
from ..ideologies import load_ideologies_file, serialize_ideologies_file
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
    _parse_tag_file_mapping,
    country_tag_entries,
    load_vanilla_tags,
    remove_country_tag_entries,
    sort_generated_country_tags,
)
from ..textio import encode_text, read_text
from ..types import (
    SaveResult,
)
from ._loading import LoadingLayer
from ._preview import PreviewLayer
from ._shared import (
    ExternalModificationError,
)
from ._sources import SourcesLayer


class PersistenceLayer(PreviewLayer, LoadingLayer, SourcesLayer):
    """Rendering pending edits to files, diffs, atomic save, discard and transactions."""

    # ── Preview & Save ───────────────────────────────────────────

    def preview_summary(self) -> str:
        lines = self._semantic_preview_lines()
        lines.extend(f"support script: {p.relative_to(self.mod_root)}" for p in self._script_file_updates)
        if not lines:
            return "No semantic changes detected"
        return "Changed:\n" + "\n".join(f"- {line}" for line in lines)

    def preview(self) -> str:
        diffs: list[str] = []
        rendered = self._render_dirty_files()
        self._assert_no_external_modifications(rendered)
        for path, current in rendered.items():
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

    def save(self, require_changes: bool = False) -> SaveResult:
        if not self._dirty:
            message = "No changes written: save() was called with no dirty changes"
            warnings.warn(message, RuntimeWarning, stacklevel=2)
            if require_changes:
                raise RuntimeError(message)
            return SaveResult(written_files=[], dirty_sections=[], no_changes=True, message=message)

        dirty_sections = sorted(self._dirty)
        rendered = self._render_dirty_files()
        self._assert_no_external_modifications(rendered)
        changed = {
            path: content
            for path, content in rendered.items()
            if (content is None and path.exists())
            or (content is not None and self._read_current_text(path) != content)
        }
        changed_assets = {
            path: content
            for path, content in self._pending_asset_writes.items()
            if self._pending_asset_baselines[path] != content
        }
        if not changed and not changed_assets:
            message = "No files written after processing dirty sections"
            if require_changes:
                raise RuntimeError(message)
            return SaveResult(dirty_sections=dirty_sections, no_changes=True, message=message)
        self._commit_rendered_files(changed, changed_assets)
        written_files = sorted((*changed, *changed_assets))
        self.discard()
        result = SaveResult(
            written_files=written_files,
            dirty_sections=dirty_sections,
            no_changes=False,
            message=f"Saved {len(written_files)} file(s)",
        )
        return result

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
                declared_in_mod = tag in self._country_tag_mappings.get(self.mod_root, {})
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
            if (color_updates or deleted_color_tags) and self._base_content_root is not None and colors_path not in self._script_file_updates:
                vanilla_colors_path = (
                    self._base_content_root / "common" / "countries" / "colors.txt"
                )
                if vanilla_colors_path.is_file():
                    colors_base = seed_country_colors_file(
                        colors_base,
                        read_text(vanilla_colors_path),
                    )
                elif set(color_updates) & self._vanilla_tags:
                    raise RuntimeError(
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
                    and self._base_content_root is not None
                ):
                    # A same-path override shadows the entire inherited file.
                    # Seed both text and models: the serializer removes definitions
                    # missing from its list. The fallback applies layer precedence
                    # and replace_path; even an empty writable file wins over it.
                    source = self._base_content_root / path.relative_to(self.mod_root)
                    if source.is_file():
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
                rendered[path] = dynamic_text if dynamic_text.strip() else None

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
                        and path.relative_to(self.mod_root)
                        in self._content_layers.lower_sources
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
        return resolved

    def _capture_source_baseline(self) -> None:
        """Fingerprint text sources so legacy writers cannot be overwritten silently."""

        suffixes = {".txt", ".yml", ".gfx", ".lua", ".mod"}
        self._source_baseline = {
            path.resolve(strict=False): path.read_bytes()
            for path in self.mod_root.rglob("*")
            if path.is_file() and path.suffix.lower() in suffixes
        }

    def _assert_no_external_modifications(
        self, rendered: dict[Path, str | None]
    ) -> None:
        conflicts: list[Path] = []
        for raw_path in rendered:
            path = raw_path.resolve(strict=False)
            baseline = self._source_baseline.get(path)
            if baseline is None:
                if path.exists():
                    conflicts.append(path)
                continue
            if not path.is_file() or path.read_bytes() != baseline:
                conflicts.append(path)
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
                f"{displayed}. Call reload() and reapply the intended edit."
            )

    def _overrides_inherited(self, path: Path | None) -> bool:
        """Return whether a mod path shadows a file from the game or a base mod."""

        if path is None or self._base_content_root is None:
            return False
        try:
            relative = path.resolve(strict=False).relative_to(self.mod_root.resolve(strict=False))
        except ValueError:
            return False
        return (self._base_content_root / relative).is_file()

    def _same_as_inherited(self, path: Path, content: str) -> bool:
        """Return whether ``content`` only repeats the file ``path`` would shadow."""

        try:
            shadowed = self.content_source(path.relative_to(self.mod_root))
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
            shadowed = self.content_source(path.relative_to(self.mod_root))
        except ValueError:
            return content
        if shadowed is None or not shadowed.is_file() or shadowed == path:
            return content
        if parse(read_text(shadowed)).problems:
            return repair(content)
        return content

    def _refuse_failed_loads(self, rendered: dict[Path, str | None]) -> None:
        """Refuse to write over, or next to, a mod file that could not be loaded.

        Its content was never fully modeled, so writing it from the model
        would silently drop what the author wrote. And while it is unreadable,
        the SDK edits the inherited version of whatever it defines: writing
        that as a new override file would give the mod the same content twice.
        """

        failed = {
            diagnostic.path.resolve(strict=False): diagnostic
            for diagnostic in self._load_diagnostics
            if diagnostic.error_type != "DuplicateIdentifierError"
        }
        if not failed:
            return
        failed_folders = {path.parent: diagnostic for path, diagnostic in failed.items()}
        for path, content in rendered.items():
            resolved = path.resolve(strict=False)
            neighbour = failed_folders.get(resolved.parent)
            if content is not None and neighbour is not None and self._shadows_inherited(path):
                raise RuntimeError(
                    f"Refusing to create {path}: it would override an inherited file, but "
                    f"{neighbour.path} in the same folder failed to load "
                    f"({neighbour.error_type}: {neighbour.message}) and may already hold the "
                    "mod's version of this content. Fix that file and call reload() first."
                )
            diagnostic = failed.get(resolved)
            if diagnostic is None or not path.is_file():
                continue
            if content is not None and content == self._read_current_text(path):
                continue
            raise RuntimeError(
                f"Refusing to rewrite {path}: it failed to load "
                f"({diagnostic.error_type}: {diagnostic.message}). Fix the file "
                "and call reload() before editing it."
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
            shadowed = self.content_source(path.relative_to(self.mod_root))
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
    def transaction(self, *, save: bool = False):
        """Temporarily mutate the mod, then save or restore in-memory state.

        Use ``with mod.transaction():`` for dry runs that call ``preview()`` inside
        the block. On normal exit the transaction is discarded unless
        ``save=True`` is passed. On exceptions, in-memory state is restored.
        """
        snapshot = self._snapshot()
        try:
            yield self
            if save:
                self.save()
            else:
                self._restore(snapshot)
        except Exception:
            self._restore(snapshot)
            raise

    def discard(self) -> None:
        self._refresh_content_layers()
        self._vanilla_tags = load_vanilla_tags(self._base_content_root) if self._base_content_root else set()
        self._load_diagnostics.clear()
        self._countries.clear()
        self._characters.clear()
        self._oobs.clear()
        self._states.clear()
        self._state_ids.clear()
        self._state_source_paths.clear()
        self._inherited_state_source_paths = None
        self._events.clear()
        self._event_namespaces.clear()
        self._on_actions.clear()
        self._on_action_occurrences.clear()
        self._decisions.clear()
        self._decision_categories.clear()
        self._ideas.clear()
        self._focus_trees.clear()
        self._dirty_focus_trees.clear()
        self._dirty_focus_files.clear()
        self._dirty_countries.clear()
        self._deleted_countries.clear()
        self._created_country_tags.clear()
        self._dirty_characters.clear()
        self._dirty_character_files.clear()
        self._dirty_oobs.clear()
        self._dirty_oob_files.clear()
        self._dirty_states.clear()
        self._deleted_state_ids.clear()
        self._deleted_state_files.clear()
        self._map_file_updates.clear()
        self._dirty_events.clear()
        self._dirty_event_files.clear()
        self._event_file_namespaces.clear()
        self._dirty_on_actions.clear()
        self._dirty_on_action_files.clear()
        self._dirty_decision_categories.clear()
        self._dirty_decision_files.clear()
        self._dirty_decision_category_files.clear()
        self._dirty_ideas.clear()
        self._dirty_idea_files.clear()
        self._idea_file_containers.clear()
        self._cached_idea_file.clear()
        self._ideologies.clear()
        self._vanilla_ideologies.clear()
        self._dirty_ideologies.clear()
        self._dirty_ideology_files.clear()
        self._dynamic_modifiers.clear()
        self._dynamic_modifier_sources.clear()
        self._dirty_dynamic_modifiers.clear()
        self._dirty_dynamic_modifier_files.clear()
        self._bookmarks.clear()
        self._dirty_bookmarks.clear()
        self._dirty_bookmark_files.clear()
        self._bookmark_date_defines = None
        self._loc_entries.clear()
        self._loc_sources.clear()
        self._dirty_loc_keys.clear()
        self._dirty_loc_files.clear()
        self._foreign_loc_entries.clear()
        self._foreign_loc_sources.clear()
        self._foreign_dirty_loc_keys.clear()
        self._loaded_loc_languages.clear()
        self._pending_asset_writes.clear()
        self._pending_asset_baselines.clear()
        self._country_name_pool_updates.clear()
        self._script_file_updates.clear()
        self._format_donors.clear()
        self._original_files.clear()
        self._dirty.clear()
        self._scan_cache.clear()
        self._equipment_unlock_cache = None
        self._sprite_texture_cache = None
        self._sprite_text_parse_cache.clear()
        self._runtime_activation_parse_cache.clear()
        self._script_trigger_index_cache.clear()
        self._state_index_cache.clear()
        self._vanilla_state_loc_entries = None
        self._country_context_cache.clear()
        self._country_color_sources.clear()
        self._country_tag_mappings = {
            base: _parse_tag_file_mapping(base / "common" / "country_tags")
            for base in (self.mod_root, self._base_content_root)
            if base is not None
        }
        self._load()
        self._capture_source_baseline()

    def reload(self) -> None:
        """Discard queued changes and reload current files from disk."""

        self.discard()
