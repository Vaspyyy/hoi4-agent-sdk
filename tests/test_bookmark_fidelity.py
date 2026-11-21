"""Bookmark files are read from the syntax tree and patched span by span."""

from __future__ import annotations

import warnings
from pathlib import Path

from hoi4 import Mod
from hoi4.bookmarks import load_bookmarks_file, serialize_bookmarks_file


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_commented_and_nested_bookmarks_blocks_are_not_the_container(tmp_path: Path) -> None:
    source = """# bookmarks = { bookmark = { name = COMMENTED } }
scripted = {
\tbookmarks = { bookmark = { name = NESTED } }
}
bookmarks = {
\tbookmark = {
\t\tname = REAL
\t\tdate = 1936.1.1.12
\t\tGER = {
\t\t\tideas = { "quoted idea" # trailing comment
\t\t\t\tplain_idea }
\t\t\trequired_dlc = { "Waking the Tiger" }
\t\t}
\t}
}
"""
    path = _write(tmp_path / "file.txt", source)
    bookmarks = load_bookmarks_file(path)

    assert [bookmark.name for bookmark in bookmarks] == ["REAL"]
    country = bookmarks[0].countries[0]
    assert country.ideas == ["quoted idea", "plain_idea"]
    assert country.required_dlc == ["Waking the Tiger"]
    assert serialize_bookmarks_file(bookmarks, original=source) == source

    bookmarks[0].date = "1939.8.14.12"
    bookmarks[0].touched_fields.add("date")
    assert serialize_bookmarks_file(bookmarks, original=source) == source.replace(
        "1936.1.1.12", "1939.8.14.12"
    )


def test_every_bookmarks_block_of_a_file_is_loaded_and_patched(tmp_path: Path) -> None:
    source = """bookmarks = {
\tbookmark = { name = FIRST date = 1936.1.1.12 }
}
bookmarks = {
\tbookmark = { name = SECOND date = 1939.8.14.12 }
}
"""
    path = _write(tmp_path / "file.txt", source)
    bookmarks = load_bookmarks_file(path)

    assert [(bookmark.name, bookmark.source_index) for bookmark in bookmarks] == [
        ("FIRST", 0),
        ("SECOND", 1),
    ]
    bookmarks[1].date = "1940.1.1.12"
    bookmarks[1].touched_fields.add("date")
    assert serialize_bookmarks_file(bookmarks, original=source) == source.replace(
        "1939.8.14.12", "1940.1.1.12"
    )


def test_setting_fields_to_their_current_values_changes_no_bytes(tmp_path: Path) -> None:
    _write(tmp_path / "descriptor.mod", 'name = "test"\n')
    source = """bookmarks = {
\tbookmark = {
\t\tname = START
\t\tdesc = START_DESC
\t\tdate = 1936.1.1.12
\t\tpicture = GFX_select_date_1936
\t\tdefault_country = "GER"
\t\tGER = {
\t\t\thistory = "GER_DESC"
\t\t\tideology = fascism
\t\t\tideas = {
\t\t\t\t
\t\t\t}
\t\t\tfocuses = {
\t\t\t}
\t\t}
\t\teffect = {
\t\t\trandomize_weather = 22345
\t\t}
\t}
}
"""
    path = _write(tmp_path / "common/bookmarks/start.txt", source)
    mod = Mod(tmp_path)
    bookmark = mod.get_bookmark("START")
    mod.update_bookmark(
        "START",
        description=bookmark.description,
        date=bookmark.date,
        picture=bookmark.picture,
        default_country=bookmark.default_country,
        default=bookmark.default,
        filters=bookmark.filters,
        effect=bookmark.effect,
    )
    country = bookmark.countries[0]
    mod.update_bookmark_country(
        "START",
        "GER",
        history=country.history,
        ideology=country.ideology,
        available=country.available,
        required_dlc=list(country.required_dlc),
        ideas=list(country.ideas),
        focuses=list(country.focuses),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mod.save()

    assert path.read_text(encoding="utf-8") == source
