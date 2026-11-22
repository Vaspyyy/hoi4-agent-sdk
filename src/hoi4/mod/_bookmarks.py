"""Bookmark authoring."""

from __future__ import annotations

from pathlib import Path

from ..bookmarks import (
    DEFAULT_BOOKMARK_EFFECT,
    Bookmark,
    BookmarkCountry,
    normalize_required_dlc,
    require_bookmark_country_key,
    require_bookmark_date,
)
from ..paths import (
    resolve_mod_output_path,
    safe_file_stem,
)
from ..script import (
    normalize_block_body,
)
from ..errors import AlreadyExistsError, NotFoundError, already_exists, not_found
from ._core import CoreLayer
from ._shared import MISSING, _D, _Missing
from ._shared import (
    _check_fields,
    _set_fields,
)


def _missing_bookmark_country(
    bookmark: Bookmark, tag: str, occurrence: int, count: int
) -> NotFoundError:
    return NotFoundError(
        f"Bookmark '{bookmark.name}' has no entry {occurrence} for country '{tag}' "
        f"({count} entries for that tag).",
        kind="bookmark country",
        id=tag,
    )


class BookmarksLayer(CoreLayer):
    """Bookmark authoring."""

    # ── Bookmarks ────────────────────────────────────────────────

    def list_bookmarks(self) -> list[str]:
        return [bookmark.name for bookmark in self._bookmarks]

    def get_bookmark(self, name: str, *, default: _D | _Missing = MISSING) -> Bookmark | _D:
        """Return the bookmark called ``name``.

        Args:
            default: Returned instead of raising ``NotFoundError`` when it
                does not exist. Without it, a missing id raises.

        Raises:
            NotFoundError: No bookmark has this name.
        """
        bookmark = next((item for item in self._bookmarks if item.name == name), None)
        if bookmark is None:
            if default is not MISSING:
                return default
            raise not_found("bookmark", name, self.list_bookmarks())
        return bookmark

    def create_bookmark(
        self,
        name: str,
        *,
        description: str = "",
        date: str = "1936.1.1.12",
        picture: str = "GFX_select_date_1936",
        default_country: str = "",
        default: bool | None = None,
        filters: str = "",
        effect: str = DEFAULT_BOOKMARK_EFFECT,
        countries: list[BookmarkCountry] | None = None,
        path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Bookmark:
        existing = next((item for item in self._bookmarks if item.name == name), None)
        if existing is not None and not overwrite:
            raise AlreadyExistsError(f"Bookmark '{name}' already exists")
        if existing is not None:
            self.delete_bookmark(name)
        target = (
            resolve_mod_output_path(self.mod_root, path)
            if path is not None
            else self.mod_root
            / "common"
            / "bookmarks"
            / f"{safe_file_stem(name, fallback='bookmark')}.txt"
        )
        selected_countries = list(countries or [])
        for country in selected_countries:
            country.tag = require_bookmark_country_key(country.tag)
            country.required_dlc = normalize_required_dlc(country.required_dlc)
        bookmark = Bookmark(
            name=name,
            description=description,
            date=date,
            picture=picture,
            default_country=default_country,
            default=default,
            filters=normalize_block_body(filters),
            effect=normalize_block_body(effect),
            countries=selected_countries,
            path=target,
        )
        self._bookmarks.append(bookmark)
        self._dirty_bookmarks.add((target, None))
        self._dirty_bookmark_files.add(target)
        self._dirty.add("bookmarks")
        return bookmark

    def update_bookmark(self, name: str, **kwargs) -> Bookmark:
        """Patch fields of a bookmark; ``path=`` moves it to another file.

        Returns:
            The updated bookmark.

        Raises:
            NotFoundError: No bookmark is called ``name``.
            TypeError: A field name is unknown; nothing changed.
        """
        bookmark = self.get_bookmark(name)
        forbidden = {"raw_block", "source_index", "touched_fields"} & set(kwargs)
        if forbidden:
            raise ValueError(f"Internal bookmark fields: {sorted(forbidden)}")
        _check_fields("update_bookmark", bookmark, kwargs, allow_path=True)
        old_path = bookmark.path
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        if "filters" in kwargs:
            kwargs["filters"] = normalize_block_body(kwargs["filters"])
        if "effect" in kwargs:
            kwargs["effect"] = normalize_block_body(kwargs["effect"])
        _set_fields(bookmark, kwargs, allow_path=True)
        bookmark.touched_fields.update(key for key in kwargs if key != "path")
        if old_path is not None:
            self._dirty_bookmark_files.add(old_path)
        if bookmark.path is not None:
            self._dirty_bookmark_files.add(bookmark.path)
            self._dirty_bookmarks.add((bookmark.path, bookmark.source_index))
        self._dirty.add("bookmarks")
        return bookmark

    def add_bookmark_country(self, bookmark_name: str, country: BookmarkCountry) -> BookmarkCountry:
        """Append a country entry to a bookmark.

        Returns:
            The added entry (the object passed in, now part of the bookmark).

        Raises:
            NotFoundError: No bookmark is called ``bookmark_name``.
            AlreadyExistsError: The bookmark already has an entry for this tag
                with the same ``required_dlc``; use ``update_bookmark_country()``.
        """
        bookmark = self.get_bookmark(bookmark_name)
        tag = require_bookmark_country_key(country.tag)
        required_dlc = normalize_required_dlc(country.required_dlc)
        if any(
            existing.tag == tag and existing.required_dlc == required_dlc
            for existing in bookmark.countries
        ):
            raise already_exists(
                "bookmark country",
                tag,
                hint=f"Bookmark '{bookmark_name}' has an entry for it; "
                "use update_bookmark_country() to change it.",
            )
        country.tag = tag
        country.required_dlc = required_dlc
        bookmark.countries.append(country)
        if bookmark.path is not None:
            self._dirty_bookmark_files.add(bookmark.path)
            self._dirty_bookmarks.add((bookmark.path, bookmark.source_index))
        self._dirty.add("bookmarks")
        return country

    def update_bookmark_country(
        self,
        bookmark_name: str,
        tag: str,
        *,
        occurrence: int = 0,
        **kwargs,
    ) -> BookmarkCountry:
        """Patch one country entry of a bookmark (zero-based ``occurrence`` of ``tag``).

        Returns:
            The updated entry.

        Raises:
            NotFoundError: The bookmark, or that entry, does not exist.
            TypeError: A field name is unknown; nothing changed.
        """
        bookmark = self.get_bookmark(bookmark_name)
        tag = require_bookmark_country_key(tag)
        matches = [country for country in bookmark.countries if country.tag == tag]
        if occurrence < 0 or occurrence >= len(matches):
            raise _missing_bookmark_country(bookmark, tag, occurrence, len(matches))
        country = matches[occurrence]
        forbidden = {"tag", "raw_block", "source_index", "touched_fields"} & set(kwargs)
        if forbidden:
            raise ValueError(f"Immutable/internal bookmark country fields: {sorted(forbidden)}")
        _check_fields("update_bookmark_country", country, kwargs)
        if "available" in kwargs:
            kwargs["available"] = normalize_block_body(kwargs["available"])
        if "required_dlc" in kwargs:
            kwargs["required_dlc"] = normalize_required_dlc(kwargs["required_dlc"])
        _set_fields(country, kwargs)
        country.touched_fields.update(kwargs)
        if bookmark.path is not None:
            self._dirty_bookmark_files.add(bookmark.path)
            self._dirty_bookmarks.add((bookmark.path, bookmark.source_index))
        self._dirty.add("bookmarks")
        return country

    def delete_bookmark_country(
        self,
        bookmark_name: str,
        tag: str,
        *,
        occurrence: int = 0,
    ) -> None:
        """Remove one country entry from a bookmark.

        ``occurrence`` picks among repeated entries of the same tag (zero-based).

        Raises:
            NotFoundError: The bookmark, or that occurrence of ``tag`` in it,
                does not exist.
        """
        bookmark = self.get_bookmark(bookmark_name)
        tag = require_bookmark_country_key(tag)
        indices = [index for index, country in enumerate(bookmark.countries) if country.tag == tag]
        if occurrence < 0 or occurrence >= len(indices):
            raise _missing_bookmark_country(bookmark, tag, occurrence, len(indices))
        del bookmark.countries[indices[occurrence]]
        if bookmark.path is not None:
            self._dirty_bookmark_files.add(bookmark.path)
            self._dirty_bookmarks.add((bookmark.path, bookmark.source_index))
        self._dirty.add("bookmarks")

    def delete_bookmark(self, name: str) -> None:
        """Stage the deletion of a bookmark.

        Raises:
            NotFoundError: No bookmark is called ``name``.
        """
        bookmark = self.get_bookmark(name)
        self._bookmarks.remove(bookmark)
        if bookmark.path is not None:
            self._dirty_bookmark_files.add(bookmark.path)
        self._dirty.add("bookmarks")

    def set_bookmark_date_range(
        self,
        start_date: str,
        end_date: str,
        *,
        path: str | Path = "common/defines/zz_bookmark_dates.lua",
    ) -> None:
        """Stage START_DATE/END_DATE defines in the normal preview/save transaction.

        Raises:
            ValueError: A date is not a valid ``Y.M.D[.H]`` date.
        """

        start = require_bookmark_date(start_date, label="bookmark start date")
        end = require_bookmark_date(end_date, label="bookmark end date")
        target = resolve_mod_output_path(self.mod_root, path)
        if target not in self._original_files:
            self._original_files[target] = self._read_current_text(target)
        self._bookmark_date_defines = (target, start, end)
        self._dirty.add("bookmark_dates")
