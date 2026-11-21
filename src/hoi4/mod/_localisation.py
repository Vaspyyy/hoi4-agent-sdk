"""Localisation tables: loading, lookup, edits and language mirroring."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

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
    normalize_localisation_language,
    normalize_localization_key,
    parse_localization_dir,
)
from ..paths import (
    resolve_mod_output_path,
)
from ..types import (
    Country,
)
from ._core import CoreLayer


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
            assert self._base_content_root is not None
            inherited = self._base_content_root / "localisation" / language
            entries, sources = parse_localization_dir(inherited)
            loc_entries.update(entries)
            for key, source in sources.items():
                target = self.mod_root / source.relative_to(self._base_content_root)
                loc_sources[key] = target
                if target not in self._original_files:
                    self._original_files[target] = source.read_text(encoding="utf-8-sig", errors="ignore")
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
                self._original_files[path] = path.read_text(
                    encoding="utf-8-sig", errors="ignore"
                )

    @property
    def default_loc_file(self) -> Path:
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
        self, key: str, *, language: str = DEFAULT_LOCALISATION_LANGUAGE
    ) -> Optional[str]:
        """Return one language's text for ``key``; no cross-language fallback."""
        _, entries, _, _ = self._loc_tables(language)
        return entries.get(self._normalize_loc_key(key))

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
        file_path: str | Path | None = None,
        *,
        language: str = DEFAULT_LOCALISATION_LANGUAGE,
        replace: bool = False,
    ) -> None:
        """Set one language's entry (English by default).

        Custom destinations must be ``.yml`` files under
        ``localisation/<language>``. ``replace=True`` routes the entry to
        ``localisation/<language>/replace/`` (``mod_replace_l_<language>.yml``
        unless ``file_path`` names another file there), which is how a mod
        overrides keys defined by the base game.
        """
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
                    f"set_loc(replace=True) needs a file under localisation/{language}/replace"
                )
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
        self._dirty.add("localization")
        dirty_keys.add(key)
        if old_source is not None:
            self._dirty_loc_files.add(old_source)
        self._dirty_loc_files.add(sources[key])

    def delete_loc(
        self, key: str, *, language: str = DEFAULT_LOCALISATION_LANGUAGE
    ) -> bool:
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
        _, entries, _, _ = self._loc_tables(language)
        q = query.lower()
        return {k: v for k, v in entries.items() if q in k.lower() or q in v.lower()}

    def all_loc(self, *, language: str = DEFAULT_LOCALISATION_LANGUAGE) -> dict[str, str]:
        _, entries, _, _ = self._loc_tables(language)
        return dict(entries)

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
        becomes ``localisation/german/replace/x_l_german.yml``. Returns the
        keys written per language.
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
                raise KeyError(f"No English localization for: {', '.join(missing)}")
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
                self.set_loc(key, value, file_path, language=language)
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
        self.delete_loc(key)
        for language in self._shipped_loc_languages():
            self.delete_loc(key, language=language)

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
