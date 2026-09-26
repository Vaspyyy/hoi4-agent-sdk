"""HOI4 bookmark models and lossless file editing.

Bookmark files frequently repeat the same country tag for different DLC
variants.  Countries are therefore represented as an ordered list and never a
dictionary keyed by tag.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .parser import find_assignment_block, strip_comments
from .patching import append_assignment, replace_assignment, set_block, set_scalar
from .patching import top_level_assignments
from .script import pdx_string, pdx_value


_COUNTRY_TAG = re.compile(r"^[A-Z0-9]{3}$")
BOOKMARK_OTHER_COUNTRY = "---"
DEFAULT_BOOKMARK_EFFECT = "randomize_weather = 22345"


@dataclass
class BookmarkCountry:
    tag: str
    history: str = ""
    ideology: str = ""
    available: str = ""
    required_dlc: list[str] = field(default_factory=list)
    ideas: list[str] = field(default_factory=list)
    focuses: list[str] = field(default_factory=list)
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set)
    source_index: int | None = None


@dataclass
class Bookmark:
    name: str
    description: str = ""
    date: str = "1936.1.1.12"
    picture: str = "GFX_select_date_1936"
    default_country: str = ""
    default: bool | None = None
    filters: str = ""
    effect: str = DEFAULT_BOOKMARK_EFFECT
    countries: list[BookmarkCountry] = field(default_factory=list)
    path: Path | None = None
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set)
    source_index: int | None = None


def load_bookmarks_file(path: Path) -> list[Bookmark]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    outer = find_assignment_block(text, "bookmarks")
    if outer is None:
        return []
    result: list[Bookmark] = []
    source_index = 0
    for span in top_level_assignments(outer[0]):
        if (
            span.key != "bookmark"
            or not span.is_block
            or span.body_start is None
            or span.body_end is None
        ):
            continue
        body = outer[0][span.body_start : span.body_end]
        result.append(_parse_bookmark(body, path, source_index))
        source_index += 1
    return result


def load_bookmarks(root: Path) -> list[Bookmark]:
    directory = root / "common" / "bookmarks"
    if not directory.is_dir():
        return []
    result: list[Bookmark] = []
    for path in sorted(directory.glob("*.txt")):
        result.extend(load_bookmarks_file(path))
    return result


def _parse_bookmark(body: str, path: Path, source_index: int) -> Bookmark:
    scalars: dict[str, str] = {}
    filters = ""
    effect = ""
    countries: list[BookmarkCountry] = []
    country_index = 0
    for span in top_level_assignments(body):
        if not span.is_block:
            scalars[span.key] = _unquote(body[span.value_start : span.value_end])
            continue
        if span.body_start is None or span.body_end is None:
            continue
        child_body = body[span.body_start : span.body_end]
        if span.key == "filters":
            filters = child_body
        elif span.key == "effect":
            effect = child_body
        elif is_bookmark_country_key(span.key):
            countries.append(_parse_country(span.key, child_body, country_index))
            country_index += 1
    default_raw = scalars.get("default")
    return Bookmark(
        name=scalars.get("name", ""),
        description=scalars.get("desc", ""),
        date=scalars.get("date", "1936.1.1.12"),
        picture=scalars.get("picture", "GFX_select_date_1936"),
        default_country=scalars.get("default_country", ""),
        default=None if default_raw is None else default_raw.lower() == "yes",
        filters=filters,
        effect=effect,
        countries=countries,
        path=path,
        raw_block=body,
        source_index=source_index,
    )


def _parse_country(tag: str, body: str, source_index: int) -> BookmarkCountry:
    scalars: dict[str, str] = {}
    blocks: dict[str, str] = {}
    required_dlc: list[str] = []
    for span in top_level_assignments(body):
        if span.is_block and span.body_start is not None and span.body_end is not None:
            child_body = body[span.body_start : span.body_end]
            if span.key == "required_dlc":
                required_dlc.extend(_bare_tokens(child_body))
            else:
                blocks[span.key] = child_body
        elif not span.is_block:
            value = _unquote(body[span.value_start : span.value_end])
            if span.key == "required_dlc":
                required_dlc.append(value)
            else:
                scalars[span.key] = value
    return BookmarkCountry(
        tag=tag,
        history=scalars.get("history", ""),
        ideology=scalars.get("ideology", ""),
        available=blocks.get("available", ""),
        required_dlc=required_dlc,
        ideas=_bare_tokens(blocks.get("ideas", "")),
        focuses=_bare_tokens(blocks.get("focuses", "")),
        raw_block=body,
        source_index=source_index,
    )


def serialize_bookmark_country(country: BookmarkCountry, indent: int = 0) -> str:
    body = _patch_country_body(country.raw_block, country)
    if not country.raw_block:
        body = _patch_country_body("", country, all_fields=True)
    return _wrap_block(pdx_string(country.tag), body, indent)


def _patch_country_body(
    body: str,
    country: BookmarkCountry,
    *,
    all_fields: bool = False,
) -> str:
    fields = {
        "history",
        "ideology",
        "available",
        "required_dlc",
        "ideas",
        "focuses",
    } if all_fields else country.touched_fields
    for field_name in sorted(fields):
        if field_name == "history":
            value = getattr(country, field_name)
            body = set_scalar(body, field_name, pdx_string(value) if value else None)
        elif field_name == "required_dlc":
            values = normalize_required_dlc(country.required_dlc)
            rendered = " ".join(pdx_string(value) for value in values)
            body = set_block(body, field_name, rendered or None)
        elif field_name == "ideology":
            body = set_scalar(body, field_name, pdx_value(country.ideology) if country.ideology else None)
        elif field_name == "available":
            body = set_block(body, field_name, country.available or None)
        elif field_name in {"ideas", "focuses"}:
            values = getattr(country, field_name)
            body = set_block(body, field_name, " ".join(values) or None)
        else:
            raise ValueError(f"Unknown bookmark country field: {field_name}")
    return body


def serialize_bookmark(bookmark: Bookmark, indent: int = 0) -> str:
    body = _patch_bookmark_body(bookmark.raw_block, bookmark)
    if not bookmark.raw_block:
        body = _patch_bookmark_body("", bookmark, all_fields=True)
    return _wrap_block("bookmark", body, indent)


def _patch_bookmark_body(
    body: str,
    bookmark: Bookmark,
    *,
    all_fields: bool = False,
) -> str:
    fields = {
        "name",
        "description",
        "date",
        "picture",
        "default_country",
        "default",
        "filters",
        "effect",
    } if all_fields else bookmark.touched_fields
    scalar_keys = {
        "name": "name",
        "description": "desc",
        "date": "date",
        "picture": "picture",
        "default_country": "default_country",
    }
    for field_name in sorted(fields):
        if field_name in scalar_keys:
            value = getattr(bookmark, field_name)
            body = set_scalar(body, scalar_keys[field_name], pdx_value(value) if value else None)
        elif field_name == "default":
            value = None if bookmark.default is None else ("yes" if bookmark.default else "no")
            body = set_scalar(body, "default", value)
        elif field_name == "filters":
            body = set_block(body, "filters", bookmark.filters or None)
        elif field_name == "effect":
            # Effect blocks conventionally follow the country entries. Handle
            # this after country patching so a new bookmark matches that shape.
            continue
        else:
            raise ValueError(f"Unknown bookmark field: {field_name}")
    body = _patch_bookmark_countries(body, bookmark.countries)
    if "effect" in fields:
        body = set_block(body, "effect", bookmark.effect or None)
    return body


def _patch_bookmark_countries(body: str, countries: list[BookmarkCountry]) -> str:
    remaining = list(countries)
    spans = top_level_assignments(body)
    country_spans = [
        (country_index, span)
        for country_index, span in enumerate(
            span
            for span in spans
            if span.is_block
            and span.body_start is not None
            and span.body_end is not None
            and is_bookmark_country_key(span.key)
        )
    ]
    for source_index, span in reversed(country_spans):
        match_index = next(
            (
                i
                for i, country in enumerate(remaining)
                if country.source_index == source_index and country.tag == span.key
            ),
            None,
        )
        # Loaded entries belong to one source occurrence. New entries have no
        # source index and are appended below, even when their tag already exists.
        if match_index is None:
            body = replace_assignment(body, span, None)
            continue
        country = remaining.pop(match_index)
        if country.raw_block and not country.touched_fields:
            continue
        patched = _patch_country_body(country.raw_block, country)
        if country.raw_block:
            body = body[: span.body_start] + patched + body[span.body_end :]
        else:
            body = replace_assignment(body, span, serialize_bookmark_country(country, 0))
    for country in remaining:
        body = append_assignment(body, serialize_bookmark_country(country, 0))
    return body


def serialize_bookmarks_file(bookmarks: list[Bookmark], *, original: str = "") -> str:
    if not original:
        body = "\n".join(serialize_bookmark(bookmark, 0) for bookmark in bookmarks)
        return _wrap_block("bookmarks", body, 0) + "\n"
    outer = find_assignment_block(original, "bookmarks")
    if outer is None:
        body = "\n".join(serialize_bookmark(bookmark, 0) for bookmark in bookmarks)
        return append_assignment(original, _wrap_block("bookmarks", body, 0))
    body, start, end = outer
    open_brace = original.find("{", start, end)
    if open_brace < 0:
        raise ValueError("Malformed bookmarks block")
    remaining = list(bookmarks)
    spans = top_level_assignments(body)
    for span in sorted(spans, key=lambda item: item.start, reverse=True):
        if span.key != "bookmark" or not span.is_block:
            continue
        source_index = sum(
            1
            for prior in spans
            if prior.key == "bookmark" and prior.is_block and prior.start < span.start
        )
        match_index = next(
            (i for i, bookmark in enumerate(remaining) if bookmark.source_index == source_index),
            None,
        )
        if match_index is None:
            body = replace_assignment(body, span, None)
            continue
        bookmark = remaining.pop(match_index)
        # Reconcile countries even when no surviving field was touched: the
        # list may have lost entries (including its final country). Untouched
        # country blocks are retained verbatim by the patcher.
        if span.body_start is None or span.body_end is None:
            continue
        patched = _patch_bookmark_body(bookmark.raw_block, bookmark)
        body = body[: span.body_start] + patched + body[span.body_end :]
    for bookmark in remaining:
        body = append_assignment(body, serialize_bookmark(bookmark, 0))
    return original[: open_brace + 1] + body + original[end - 1 :]


def patch_bookmark_dates_defines(
    original: str,
    *,
    start_date: str,
    end_date: str,
) -> str:
    """Patch ``NDefines.NGame`` date overrides without rewriting other defines."""

    result = set_scalar(original, "NDefines.NGame.START_DATE", pdx_string(start_date))
    return set_scalar(result, "NDefines.NGame.END_DATE", pdx_string(end_date))


def require_bookmark_date(value: str, *, label: str = "bookmark date") -> str:
    """Validate and return a HOI4 date in ``YYYY.M.D[.H]`` form."""

    text = str(value)
    parts = text.split(".")
    if len(parts) not in {3, 4} or not all(part.isdigit() for part in parts):
        raise ValueError(f"Invalid {label} {value!r}; expected YYYY.M.D or YYYY.M.D.H")
    year, month, day = (int(part) for part in parts[:3])
    hour = int(parts[3]) if len(parts) == 4 else 0
    if year < 1 or not 1 <= month <= 12 or not 1 <= day <= 31 or not 0 <= hour <= 23:
        raise ValueError(f"Invalid {label} {value!r}")
    return text


def _bare_tokens(body: str) -> list[str]:
    if not body:
        return []
    tokens: list[str] = []
    for part in re.findall(r'"(?:\\.|[^"\\])*"|\S+', strip_comments(body)):
        tokens.append(_unquote(part))
    return tokens


def is_bookmark_country_key(value: str) -> bool:
    """Return whether ``value`` is a country tag or vanilla's ``---`` separator."""

    return value == BOOKMARK_OTHER_COUNTRY or _COUNTRY_TAG.fullmatch(value) is not None


def require_bookmark_country_key(value: str) -> str:
    """Normalize a bookmark country key while retaining the ``---`` pseudo-country."""

    normalized = str(value).upper()
    if not is_bookmark_country_key(normalized):
        raise ValueError(
            f"Invalid bookmark country key {value!r}; expected a 3-character tag or '---'"
        )
    return normalized


def normalize_required_dlc(values: Iterable[str] | str | None) -> list[str]:
    """Validate and copy a bookmark country's ordered DLC requirement list."""

    if values is None:
        return []
    selected = (values,) if isinstance(values, str) else values
    result: list[str] = []
    for value in selected:
        text = str(value)
        if not text:
            continue
        if any(character in text for character in "\r\n\x00"):
            raise ValueError(f"Invalid required DLC name: {value!r}")
        result.append(text)
    return result


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _wrap_block(key: str, body: str, indent: int) -> str:
    prefix = "\t" * indent
    clean = body.strip("\n")
    if not clean.strip():
        return f"{prefix}{key} = {{ }}"
    inner = "\n".join(prefix + "\t" + line for line in clean.splitlines())
    return f"{prefix}{key} = {{\n{inner}\n{prefix}}}"
