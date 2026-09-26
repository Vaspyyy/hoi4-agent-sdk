from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.bookmarks import Bookmark, BookmarkCountry, load_bookmarks_file
from hoi4.bookmarks import patch_bookmark_dates_defines, serialize_bookmarks_file
from hoi4.parser import parse_pdx


SOURCE = '''# preserve header
bookmarks = {
    bookmark = {
        name = "BOOKMARK_NAME"
        desc = "BOOKMARK_DESC"
        date = 1936.1.1.12
        picture = GFX_select_date_1936
        default_country = "GER"
        default = yes
        filters = { ALL }

        "GER" = {
            history = GER_BOOKMARK_DESC
            ideology = fascism
            required_dlc = { "Man the Guns" "No Step Back" }
            ideas = { GER_idea }
            focuses = { GER_focus }
            unknown_new_field = yes # preserve
        }
        "GER" = {
            available = { has_dlc = "Alt History" }
            history = GER_DLC_BOOKMARK_DESC
            ideology = neutrality
        }
        "---" = {
            history = "OTHER_COUNTRIES_DESC"
            unknown_separator_field = yes # preserve separator metadata
        }
        effect = {
            randomize_weather = 22345 # obligatory seed
            future_effect = { keep = yes }
        }
    }
}
'''


def test_bookmark_loader_preserves_duplicate_country_variants(tmp_path: Path) -> None:
    path = tmp_path / "bookmark.txt"
    path.write_text(SOURCE, encoding="utf-8")

    bookmarks = load_bookmarks_file(path)

    assert len(bookmarks) == 1
    assert bookmarks[0].name == "BOOKMARK_NAME"
    assert "randomize_weather = 22345" in bookmarks[0].effect
    assert [country.tag for country in bookmarks[0].countries] == ["GER", "GER", "---"]
    assert bookmarks[0].countries[0].required_dlc == ["Man the Guns", "No Step Back"]
    assert bookmarks[0].countries[1].available.strip() == 'has_dlc = "Alt History"'


def test_bookmark_noop_is_byte_identical(tmp_path: Path) -> None:
    path = tmp_path / "bookmark.txt"
    path.write_text(SOURCE, encoding="utf-8")
    bookmarks = load_bookmarks_file(path)

    assert serialize_bookmarks_file(bookmarks, original=SOURCE) == SOURCE


def test_editing_second_duplicate_only_changes_that_variant(tmp_path: Path) -> None:
    path = tmp_path / "bookmark.txt"
    path.write_text(SOURCE, encoding="utf-8")
    bookmarks = load_bookmarks_file(path)
    second = bookmarks[0].countries[1]
    second.ideology = "democratic"
    second.touched_fields.add("ideology")

    rendered = serialize_bookmarks_file(bookmarks, original=SOURCE)

    assert rendered.count("ideology = fascism") == 1
    assert rendered.count("ideology = democratic") == 1
    assert "unknown_new_field = yes # preserve" in rendered
    parse_pdx(rendered)


def test_removing_header_scalar_keeps_duplicate_country_occurrences_stable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bookmark.txt"
    path.write_text(SOURCE, encoding="utf-8")
    bookmark = load_bookmarks_file(path)[0]
    bookmark.description = ""
    bookmark.touched_fields.add("description")
    second = bookmark.countries[1]
    second.ideology = "democratic"
    second.touched_fields.add("ideology")

    rendered = serialize_bookmarks_file([bookmark], original=SOURCE)

    assert "desc =" not in rendered
    assert rendered.count("history = GER_BOOKMARK_DESC") == 1
    assert rendered.count("history = GER_DLC_BOOKMARK_DESC") == 1
    first_start = rendered.index('"GER" = {')
    second_start = rendered.index('"GER" = {', first_start + 1)
    first_block = rendered[first_start:second_start]
    second_block = rendered[second_start : rendered.index('"---" = {')]
    assert "ideology = fascism" in first_block
    assert "unknown_new_field = yes # preserve" in first_block
    assert "ideology = democratic" in second_block
    assert 'available = { has_dlc = "Alt History" }' in second_block
    parse_pdx(rendered)


def test_editing_bookmark_effect_only_preserves_unrelated_source(tmp_path: Path) -> None:
    path = tmp_path / "bookmark.txt"
    path.write_text(SOURCE, encoding="utf-8")
    bookmark = load_bookmarks_file(path)[0]
    bookmark.effect = "randomize_weather = 54321\nfuture_effect = { keep = yes }"
    bookmark.touched_fields.add("effect")

    rendered = serialize_bookmarks_file([bookmark], original=SOURCE)

    assert "randomize_weather = 54321" in rendered
    assert "randomize_weather = 22345" not in rendered
    assert "future_effect = { keep = yes }" in rendered
    assert "unknown_new_field = yes # preserve" in rendered
    assert "# preserve header" in rendered
    parse_pdx(rendered)


def test_editing_required_dlc_and_other_country_preserves_vanilla_shapes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bookmark.txt"
    path.write_text(SOURCE, encoding="utf-8")
    bookmark = load_bookmarks_file(path)[0]
    first = bookmark.countries[0]
    first.required_dlc = ["Arms Against Tyranny", "No Compromise, No Surrender"]
    first.touched_fields.add("required_dlc")
    other = bookmark.countries[2]
    other.history = "UPDATED_OTHER_COUNTRIES_DESC"
    other.touched_fields.add("history")

    rendered = serialize_bookmarks_file([bookmark], original=SOURCE)

    assert 'required_dlc = { "Arms Against Tyranny" "No Compromise, No Surrender" }' in rendered
    assert '"---" = {' in rendered
    assert 'history = "UPDATED_OTHER_COUNTRIES_DESC"' in rendered
    assert "unknown_separator_field = yes # preserve separator metadata" in rendered
    assert "Man the Guns" not in rendered
    parse_pdx(rendered)


def test_new_bookmark_and_defines_serialize() -> None:
    bookmark = Bookmark(
        name="NEW_BOOKMARK",
        description="NEW_BOOKMARK_DESC",
        date="1940.1.1.12",
        default_country="ABC",
        countries=[
            BookmarkCountry(tag="ABC", history="ABC_BOOKMARK_DESC", ideology="democratic")
        ],
    )

    rendered = serialize_bookmarks_file([bookmark])
    defines = patch_bookmark_dates_defines(
        "NDefines.NGame.START_DATE = \"1936.1.1.12\"\n# keep\n",
        start_date="1940.1.1.12",
        end_date="1960.1.1.1",
    )

    assert '"ABC" = {' in rendered
    assert "1940.1.1.12" in rendered
    assert "effect = {" in rendered
    assert "randomize_weather = 22345" in rendered
    assert '# keep' in defines
    assert 'NDefines.NGame.END_DATE = "1960.1.1.1"' in defines
    parse_pdx(rendered)


@pytest.mark.parametrize("edit_bookmark", [False, True])
@pytest.mark.parametrize("case", ["unique", "final", "first_variant", "second_variant"])
def test_country_deletion_preview_save_reload(
    tmp_path: Path, case: str, edit_bookmark: bool,
) -> None:
    source = SOURCE.replace('"---" = {', '"FRA" = {')
    if case == "final":
        start = source.index('        "GER" = {')
        end = source.index('        "FRA" = {')
        source = source[:start] + source[end:]
    path = tmp_path / "common/bookmarks/test.txt"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    mod = Mod(tmp_path)
    original_countries = list(mod.get_bookmark("BOOKMARK_NAME").countries)
    target = {"unique": 2, "final": 0, "first_variant": 0, "second_variant": 1}[case]
    removed = original_countries[target]
    survivors = original_countries[:target] + original_countries[target + 1:]

    assert mod.delete_bookmark_country(
        "BOOKMARK_NAME", removed.tag, occurrence=1 if case == "second_variant" else 0,
    )
    if edit_bookmark:
        assert mod.update_bookmark("BOOKMARK_NAME", description="UPDATED_DESC")
    preview = mod.preview()
    assert "common/bookmarks/test.txt" in preview
    assert any(
        line.startswith("-") and removed.history in line for line in preview.splitlines()
    )
    assert path.read_text(encoding="utf-8") == source
    assert mod.preview() == preview

    result = mod.save(require_changes=True)
    assert result.written_files == [path]
    rendered = path.read_text(encoding="utf-8")
    assert removed.history not in rendered
    assert "# preserve header" in rendered
    assert "randomize_weather = 22345 # obligatory seed" in rendered
    assert "future_effect = { keep = yes }" in rendered
    for survivor in survivors:
        assert survivor.raw_block in rendered
    assert [rendered.index(country.raw_block) for country in survivors] == sorted(
        rendered.index(country.raw_block) for country in survivors
    )

    reloaded = Mod(tmp_path)
    bookmark = reloaded.get_bookmark("BOOKMARK_NAME")
    assert bookmark.description == ("UPDATED_DESC" if edit_bookmark else "BOOKMARK_DESC")
    assert [country.history for country in bookmark.countries] == [
        country.history for country in survivors
    ]
    assert [country.required_dlc for country in bookmark.countries] == [
        country.required_dlc for country in survivors
    ]
    assert [country.available for country in bookmark.countries] == [
        country.available for country in survivors
    ]
    assert reloaded.preview() == ""
    assert serialize_bookmarks_file(load_bookmarks_file(path), original=rendered) == rendered


@pytest.mark.parametrize("delete_existing", [False, True])
def test_new_same_tag_country_is_appended_without_reusing_source_occurrence(
    tmp_path: Path, delete_existing: bool,
) -> None:
    path = tmp_path / "common/bookmarks/test.txt"
    path.parent.mkdir(parents=True)
    path.write_text(SOURCE, encoding="utf-8")
    mod = Mod(tmp_path)
    if delete_existing:
        assert mod.delete_bookmark_country("BOOKMARK_NAME", "GER", occurrence=1)
    added = BookmarkCountry(tag="GER", history="NEW_VARIANT", required_dlc=["New DLC"])
    mod.add_bookmark_country("BOOKMARK_NAME", added)
    assert added.source_index is None
    assert "NEW_VARIANT" in mod.preview()
    result = mod.save(require_changes=True)
    assert result.written_files == [path]

    bookmark = Mod(tmp_path).get_bookmark("BOOKMARK_NAME")
    expected = ["GER_BOOKMARK_DESC"]
    if not delete_existing:
        expected.append("GER_DLC_BOOKMARK_DESC")
    expected.extend(["OTHER_COUNTRIES_DESC", "NEW_VARIANT"])
    assert [country.history for country in bookmark.countries] == expected
    assert bookmark.countries[-1].required_dlc == ["New DLC"]
    assert "unknown_new_field = yes # preserve" in path.read_text(encoding="utf-8")
