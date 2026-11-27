"""Localisation tables: loading, lookup, edits and language mirroring."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable

from .._disk_cache import NAMES_ON_DISK
from ..characters import (
    Character,
)
from ..countries import (
    _country_localisation_suffixes,
)
from ..localisation import (
    DEFAULT_LOCALISATION_LANGUAGE,
    LOCALISATION_LANGUAGES,
    localisation_file_name,
    localisation_keys_in,
    normalize_localisation_language,
    normalize_localization_key,
    parse_localization_dir,
    parse_localization_files,
)
from ..paths import resolve_mod_output_path
from ..textio import read_text
from ..types import (
    Country,
)
from ..errors import ReadOnlyError, not_found
from ._core import CoreLayer
from ._shared import MISSING, _D, _Missing


class LocalisationLayer(CoreLayer):
    """Localisation tables: loading, lookup, edits and language mirroring."""

    def _load_localization(self) -> None:
        self._load_localization_language(
            DEFAULT_LOCALISATION_LANGUAGE, self._loc_entries, self._loc_sources
        )

    def _load_localization_language(
        self,
        language: str,
        loc_entries: dict[str, str],
        loc_sources: dict[str, Path],
    ) -> None:
        if self._content_layers is not None:
            inherited = self._content_layers.rglob(Path("localisation") / language, "*.yml")
            relatives = {source: relative for relative, source in inherited.items()}
            entries, sources = parse_localization_files(
                inherited.values(), file_entries=self._localisation_file_entries
            )
            loc_entries.update(entries)
            for key, source in sources.items():
                target = self.mod_root / relatives[source]
                loc_sources[key] = target
                if target not in self._original_files:
                    self._original_files[target] = read_text(source)
        loc_dir = self.mod_root / "localisation" / language
        if not loc_dir.exists():
            return
        entries, sources = parse_localization_dir(loc_dir)
        loc_entries.update(entries)
        loc_sources.update(sources)
        # Keep every localization source, including header/comment-only files.
        # Otherwise adding the first modeled key to such a file would serialize
        # from an empty baseline and silently discard its existing content.
        for path in loc_dir.rglob("*.yml"):
            if path.is_file():
                self._original_files[path] = self._read_source(path)

    @property
    def default_loc_file(self) -> Path:
        """The file new English entries go to when no ``path`` is given.

        It is ``localisation/english/mod_l_english.yml`` in the mod.
        """
        return self.mod_root / "localisation" / "english" / "mod_l_english.yml"

    def _default_loc_file_for(self, language: str, *, replace: bool = False) -> Path:
        """Default new-entry file; ``replace`` targets the override folder."""
        if language == DEFAULT_LOCALISATION_LANGUAGE and not replace:
            return self.default_loc_file
        folder = self.mod_root / "localisation" / language
        if replace:
            return folder / "replace" / f"mod_replace_l_{language}.yml"
        return folder / f"mod_l_{language}.yml"

    # ── Localization ─────────────────────────────────────────────

    def get_loc(
        self,
        key: str,
        *,
        language: str = DEFAULT_LOCALISATION_LANGUAGE,
        default: _D | _Missing = MISSING,
    ) -> str | _D:
        """Return one language's text for ``key``; no cross-language fallback.

        The mod's entry (with unsaved edits) wins; a key the mod does not
        define comes from the game's own files of that language.

        Args:
            default: Returned instead of raising ``NotFoundError`` when the
                key has no text in that language.

        Raises:
            NotFoundError: ``key`` has no text in that language and no
                default was given.
        """
        language, entries, _, _ = self._loc_tables(language)
        key = self._normalize_loc_key(key)
        value = entries.get(key)
        if value is None:
            value = self._game_loc(language).get(key)
        if value is None:
            if default is not MISSING:
                return default
            raise not_found(f"{language} localisation key", key, entries)
        return value

    def _game_loc(self, language: str) -> dict[str, str]:
        """The game's own entries of one language, read on first use.

        Only for a mod without base mods: with base mods, the inherited
        layers (the game among them) are already part of the mod's tables.
        Files the mod hides with ``replace_path`` are left out, and entries in
        a ``replace`` folder win over the others, as in the game. The entries
        never become the mod's: saving does not write them.
        """

        if self._content_layers is not None or self.hoi4_install is None:
            return {}
        language = normalize_localisation_language(language)
        cached = self._game_loc_entries.get(language)
        if cached is not None:
            return cached
        files = [
            (relative, path)
            for relative, path in self.content_files(Path("localisation") / language).items()
            if path.suffix.lower() == ".yml" and not self._in_mod_root(path)
        ]
        files.sort(
            key=lambda item: "replace" in (part.lower() for part in Path(item[0]).parts[2:-1])
        )
        cached = {}
        for _, path in files:
            try:
                cached.update(self._localisation_file_entries(path))
            except (OSError, ValueError):
                continue
        self._game_loc_entries[language] = cached
        return cached

    def _has_loc(self, key: str) -> bool:
        """Return whether an English localisation key exists anywhere the game looks.

        That is the mod's loaded table (with unsaved edits) and the key lines
        of every effective ``localisation/english`` file of the game, the base
        mods and the mod. The key index reads only ``KEY:0 "`` at the start of
        a line, the way the game finds keys, so a line whose text the strict
        reader rejects (such as inner quotes) still counts. For a key changed
        in memory, the mod's own files on disk no longer count; the game's
        and base mods' files still do.
        """

        key = normalize_localization_key(key)
        if not key:
            return False
        # A value with spaces is plain text the game shows as written, and a
        # bracket command (``[GetName]``, ``[CZE_title_dynamic]``) is
        # resolved at runtime by scripted localisation.
        if any(character.isspace() for character in key) or (
            key.startswith("[") and key.endswith("]")
        ):
            return True
        if key in self._loc_entries:
            return True
        inherited = self._scan_cache.get("validation_loc_keys")
        own = self._scan_cache.get("validation_mod_loc_keys")
        if inherited is None or own is None:
            inherited, own = set(), set()
            folder = Path("localisation") / DEFAULT_LOCALISATION_LANGUAGE
            for path in self.content_files(folder).values():
                if path.suffix.lower() != ".yml":
                    continue
                try:
                    keys = self._cached_result(
                        "localisation_keys", path, localisation_keys_in, NAMES_ON_DISK
                    )
                except (OSError, ValueError):
                    continue
                (own if self._in_mod_root(path) else inherited).update(keys)
            self._scan_cache["validation_loc_keys"] = inherited
            self._scan_cache["validation_mod_loc_keys"] = own
        if key in inherited:
            return True
        return key in own and key not in self._dirty_loc_keys

    @staticmethod
    def _normalize_loc_key(key: str) -> str:
        return normalize_localization_key(key)

    def _loc_tables(
        self, language: str
    ) -> tuple[str, dict[str, str], dict[str, Path], set[str]]:
        """Return ``(language, entries, sources, dirty_keys)`` for one language.

        English uses the tables loaded with the mod. Other languages are
        indexed on first use exactly like English: inherited base layers
        first, then the mod's own ``localisation/<language>`` files.
        """
        language = normalize_localisation_language(language)
        if language == DEFAULT_LOCALISATION_LANGUAGE:
            return language, self._loc_entries, self._loc_sources, self._dirty_loc_keys
        if language not in self._loaded_loc_languages:
            entries = self._foreign_loc_entries.setdefault(language, {})
            sources = self._foreign_loc_sources.setdefault(language, {})
            self._load_localization_language(language, entries, sources)
            self._loaded_loc_languages.add(language)
        return (
            language,
            self._foreign_loc_entries.setdefault(language, {}),
            self._foreign_loc_sources.setdefault(language, {}),
            self._foreign_dirty_loc_keys.setdefault(language, set()),
        )

    def set_loc(
        self,
        key: str,
        value: str,
        *,
        path: str | Path | None = None,
        language: str = DEFAULT_LOCALISATION_LANGUAGE,
        overwrite_vanilla: bool = False,
    ) -> None:
        """Set one language's entry (English by default).

        Custom destinations (``path``) must be ``.yml`` files under
        ``localisation/<language>``. ``overwrite_vanilla=True`` routes the
        entry to ``localisation/<language>/replace/``
        (``mod_replace_l_<language>.yml`` unless ``path`` names another file
        there), which is how a mod overrides keys defined by the base game.

        Raises:
            ValueError: ``path`` is outside ``localisation/<language>``.
        """
        file_path = path
        replace = overwrite_vanilla
        language, entries, sources, dirty_keys = self._loc_tables(language)
        language_dir = self.mod_root / "localisation" / language
        target = None
        if file_path is not None:
            target = resolve_mod_output_path(self.mod_root, file_path)
            if not target.is_relative_to(language_dir) or target.suffix != ".yml":
                raise ValueError(
                    f"set_loc() supports only .yml files under localisation/{language}; "
                    "other localization destinations are not loaded and cannot be safely edited"
                )
            if replace and not target.is_relative_to(language_dir / "replace"):
                raise ValueError(
                    f"set_loc(overwrite_vanilla=True) needs a file under "
                    f"localisation/{language}/replace"
                )
            self._note_target(target)
        elif replace:
            target = self._default_loc_file_for(language, replace=True)
        key = self._normalize_loc_key(key)
        old_source = sources.get(key)
        entries[key] = value
        if language == DEFAULT_LOCALISATION_LANGUAGE and key.startswith("STATE_"):
            self._state_index_cache.clear()
        if target is not None:
            sources[key] = target
        elif key not in sources:
            sources[key] = self._default_loc_file_for(language)
        # The file the key goes to may be one the loader did not read (the
        # default file behind a linked folder, which the loader skips).
        self._note_target(sources[key])
        self._dirty.add("localization")
        dirty_keys.add(key)
        if old_source is not None:
            self._dirty_loc_files.add(old_source)
        self._dirty_loc_files.add(sources[key])

    def delete_loc(
        self, key: str, *, language: str = DEFAULT_LOCALISATION_LANGUAGE
    ) -> None:
        """Stage the deletion of one language's entry of ``key``.

        Raises:
            NotFoundError: ``key`` has no entry in that language.
            ReadOnlyError: Only the game's own files define ``key``; override
                it with ``set_loc()`` instead.
        """
        if not self._delete_loc_entry(key, language):
            _, entries, _, _ = self._loc_tables(language)
            if self._normalize_loc_key(key) in self._game_loc(language):
                raise ReadOnlyError(
                    f"Localisation key '{key}' comes from the game's own files; "
                    "override it with set_loc() instead of deleting it."
                )
            raise not_found(
                f"{normalize_localisation_language(language)} localisation key",
                self._normalize_loc_key(key),
                entries,
            )

    def _delete_loc_entry(self, key: str, language: str) -> bool:
        """Delete one entry; False when there was none."""
        language, entries, sources, dirty_keys = self._loc_tables(language)
        key = self._normalize_loc_key(key)
        if key in entries:
            source = sources.get(key, self._default_loc_file_for(language))
            del entries[key]
            sources.pop(key, None)
            if language == DEFAULT_LOCALISATION_LANGUAGE and key.startswith("STATE_"):
                self._state_index_cache.clear()
            self._dirty.add("localization")
            dirty_keys.add(key)
            self._dirty_loc_files.add(source)
            return True
        return False

    def search_loc(
        self, query: str, *, language: str = DEFAULT_LOCALISATION_LANGUAGE
    ) -> dict[str, str]:
        """Return the entries whose key or text contains ``query``, ignoring case.

        Searches what ``all_loc()`` returns: the game's entries with the
        mod's on top.

        Raises:
            ValueError: ``language`` is not a HOI4 language.
        """
        q = query.lower()
        return {
            k: v
            for k, v in self.all_loc(language=language).items()
            if q in k.lower() or q in v.lower()
        }

    def all_loc(self, *, language: str = DEFAULT_LOCALISATION_LANGUAGE) -> dict[str, str]:
        """Every entry of one language: the game's, with the mod's on top.

        Raises:
            ValueError: ``language`` is not a HOI4 language.
        """
        language, entries, _, _ = self._loc_tables(language)
        merged = dict(self._game_loc(language))
        merged.update(entries)
        return merged

    def copy_loc_to_languages(
        self,
        keys: Iterable[str] | None = None,
        languages: Iterable[str] | None = None,
        *,
        overwrite: bool = False,
    ) -> dict[str, list[str]]:
        """Fill missing translations with the English text.

        ``keys`` defaults to every English key the mod itself authors
        (inherited base-game text is never copied). ``languages`` defaults to
        every supported non-English language. Existing translations are kept
        unless ``overwrite=True``. New entries go to the file mirroring their
        English source, e.g. ``localisation/english/replace/x_l_english.yml``
        becomes ``localisation/german/replace/x_l_german.yml``. Staged until
        ``save()``.

        Returns:
            The keys written, per language.

        Raises:
            NotFoundError: A key in ``keys`` has no English text in the mod.
            ValueError: A language is not a HOI4 language, or is English.
        """
        if languages is None:
            targets = [
                language
                for language in LOCALISATION_LANGUAGES
                if language != DEFAULT_LOCALISATION_LANGUAGE
            ]
        else:
            targets = []
            for raw_language in [languages] if isinstance(languages, str) else languages:
                language = normalize_localisation_language(raw_language)
                if language == DEFAULT_LOCALISATION_LANGUAGE:
                    raise ValueError("copy_loc_to_languages() copies from English; omit it")
                if language not in targets:
                    targets.append(language)
        if keys is None:
            source_keys = sorted(self._authored_loc_keys(DEFAULT_LOCALISATION_LANGUAGE))
        else:
            source_keys = list(
                dict.fromkeys(
                    self._normalize_loc_key(key)
                    for key in ([keys] if isinstance(keys, str) else keys)
                )
            )
            missing = [key for key in source_keys if key not in self._loc_entries]
            if missing:
                raise not_found(
                    "English localisation key",
                    missing[0],
                    self._loc_entries,
                    hint=f"Missing keys: {', '.join(missing)}.",
                )
        written: dict[str, list[str]] = {}
        for language in targets:
            _, entries, sources, _ = self._loc_tables(language)
            written[language] = []
            for key in source_keys:
                value = self._loc_entries[key]
                if key in entries and (not overwrite or entries[key] == value):
                    continue
                file_path = (
                    None
                    if key in sources
                    else self._mirrored_loc_file(
                        self._loc_sources.get(key, self.default_loc_file), language
                    )
                )
                self.set_loc(key, value, path=file_path, language=language)
                written[language].append(key)
        return written

    def _mirrored_loc_file(self, english_source: Path, language: str) -> Path:
        english_dir = self.mod_root / "localisation" / DEFAULT_LOCALISATION_LANGUAGE
        if not english_source.is_relative_to(english_dir):
            return self._default_loc_file_for(language)
        relative = english_source.relative_to(english_dir)
        return (
            self.mod_root
            / "localisation"
            / language
            / relative.parent
            / localisation_file_name(relative.stem, language)
        )

    def _authored_loc_keys(self, language: str) -> set[str]:
        """Keys the mod itself defines in ``language`` (not inherited text)."""
        language, entries, _, dirty_keys = self._loc_tables(language)
        if self._content_layers is None:
            return set(entries)
        on_disk, _ = parse_localization_dir(self.mod_root / "localisation" / language)
        return {key for key in set(on_disk) | dirty_keys if key in entries}

    def _shipped_loc_languages(self) -> list[str]:
        """Non-English languages with mod files on disk or pending entries."""
        shipped: list[str] = []
        for language in LOCALISATION_LANGUAGES:
            if language == DEFAULT_LOCALISATION_LANGUAGE:
                continue
            folder = self.mod_root / "localisation" / language
            has_files = folder.is_dir() and any(
                path.is_file() for path in folder.rglob("*.yml")
            )
            if has_files or self._foreign_dirty_loc_keys.get(language):
                shipped.append(language)
        return shipped

    def _delete_loc_all_languages(self, key: str) -> None:
        """Delete ``key`` in English and every language the mod ships."""
        self._delete_loc_entry(key, DEFAULT_LOCALISATION_LANGUAGE)
        for language in self._shipped_loc_languages():
            self._delete_loc_entry(key, language)

    def _sync_country_loc(self, country: Country) -> None:
        name = country.name or country.tag
        adj = country.adjective or name
        dirty_keys: list[str] = []
        target = self.mod_root / "localisation" / "english" / f"{country.tag}_country_l_english.yml"
        for suffix in _country_localisation_suffixes(country):
            k = f"{country.tag}{suffix}"
            self._loc_entries[k] = name
            self._loc_entries[f"{k}_DEF"] = name
            dirty_keys.extend([k, f"{k}_DEF"])
        self._loc_entries[f"{country.tag}_ADJ"] = adj
        dirty_keys.append(f"{country.tag}_ADJ")
        if country.leader:
            self._loc_entries[country.leader.character_id] = country.leader.name
            self._loc_entries[f"{country.leader.character_id}_desc"] = (
                f"{country.leader.name} (leader)"
            )
            dirty_keys.extend([country.leader.character_id, f"{country.leader.character_id}_desc"])
        for key in dirty_keys:
            # Existing country localization may intentionally live in a shared
            # file. Keep that routing instead of creating a second definition
            # in the generated country file. Only newly introduced keys use the
            # country-specific default target.
            self._loc_sources.setdefault(key, target)
            self._dirty_loc_files.add(self._loc_sources[key])
        self._dirty_loc_keys.update(dirty_keys)
        self._dirty.add("localization")

    def _sync_character_loc(self, character: Character) -> None:
        display_name = character.name or character.id
        target = (
            self.mod_root
            / "localisation"
            / "english"
            / f"{character.country_tag or 'mod'}_characters_l_english.yml"
        )
        values = {
            character.id: display_name,
            f"{character.id}_desc": f"{display_name}",
        }
        for key, value in values.items():
            self._loc_entries[key] = value
            self._loc_sources.setdefault(key, target)
            self._dirty_loc_files.add(self._loc_sources[key])
            self._dirty_loc_keys.add(key)
        self._dirty.add("localization")
