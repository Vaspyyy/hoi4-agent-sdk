from __future__ import annotations

from hoi4.bookmarks import Bookmark, BookmarkCountry
from hoi4.content_validation import validate_bookmark, validate_dynamic_idea_group
from hoi4.content_validation import validate_ideology
from hoi4.dynamic_ideas import DynamicIdea, DynamicIdeaGroup
from hoi4.ideologies import Ideology, SubIdeology


def test_validate_ideology_reports_bad_color_duplicate_type_and_ai() -> None:
    ideology = Ideology(
        id="bad id",
        color=(0, 256, 0),
        types=[SubIdeology("same"), SubIdeology("same")],
        ai_behavior="chaos",
    )

    codes = {issue.code for issue in validate_ideology(ideology)}

    assert codes == {
        "invalid_ideology_id",
        "invalid_ideology_color",
        "duplicate_subideology_id",
        "invalid_ideology_ai_behavior",
    }


def test_validate_dynamic_ideas_reports_duplicate_and_bad_script() -> None:
    group = DynamicIdeaGroup(
        name="valid_group",
        ideas=[
            DynamicIdea(id="repeat", potential="if = {"),
            DynamicIdea(id="repeat"),
        ],
    )

    codes = {issue.code for issue in validate_dynamic_idea_group(group)}

    assert "duplicate_dynamic_idea_id" in codes
    assert "invalid_dynamic_idea_script" in codes


def test_bookmark_duplicate_dlc_variants_are_allowed() -> None:
    bookmark = Bookmark(
        name="START",
        date="1936.1.1.12",
        default_country="GER",
        countries=[
            BookmarkCountry(tag="GER", required_dlc=["One", "Shared"]),
            BookmarkCountry(tag="GER", required_dlc=["Two", "Shared"]),
            BookmarkCountry(tag="---"),
        ],
    )

    assert validate_bookmark(bookmark) == []


def test_bookmark_reports_bad_date_missing_default_and_identical_variant() -> None:
    bookmark = Bookmark(
        name="START",
        date="1936.13.1.25",
        default_country="USA",
        countries=[
            BookmarkCountry(tag="GER", required_dlc=["Bad\nDLC"]),
            BookmarkCountry(tag="GER"),
        ],
    )

    codes = {issue.code for issue in validate_bookmark(bookmark)}

    assert codes == {
        "invalid_bookmark_date",
        "invalid_bookmark_required_dlc",
        "bookmark_default_country_missing",
        "duplicate_bookmark_country_variant",
    }


def test_bookmark_requires_valid_randomize_weather_effect() -> None:
    missing = Bookmark(name="MISSING_EFFECT", effect="add_political_power = 1")
    malformed = Bookmark(name="BAD_EFFECT", effect="randomize_weather = {")

    missing_codes = {issue.code for issue in validate_bookmark(missing)}
    malformed_codes = {issue.code for issue in validate_bookmark(malformed)}

    assert missing_codes == {"bookmark_randomize_weather_missing"}
    assert malformed_codes == {
        "invalid_bookmark_effect",
        "bookmark_randomize_weather_missing",
    }
