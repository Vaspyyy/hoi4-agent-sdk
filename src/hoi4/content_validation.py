"""Validation for the SDK's extended Studio-facing content models."""

from __future__ import annotations

import re

from .bookmarks import (
    Bookmark,
    is_bookmark_country_key,
    normalize_required_dlc,
    require_bookmark_date,
)
from .dynamic_ideas import DynamicIdeaGroup
from .ideologies import Ideology, VANILLA_AI_BEHAVIORS
from .parser import ParseError
from .patching import top_level_assignments
from .script import validate_script_syntax
from .types import ValidationError


_SCRIPT_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")


def validate_ideology(ideology: Ideology) -> list[ValidationError]:
    issues: list[ValidationError] = []
    location = str(ideology.path) if ideology.path else None
    if not _SCRIPT_ID.fullmatch(ideology.id):
        issues.append(_error(f"Invalid ideology ID '{ideology.id}'", "invalid_ideology_id", location))
    if len(ideology.color) != 3:
        issues.append(
            _error(
                f"Ideology '{ideology.id}' colour must contain exactly three RGB channels",
                "invalid_ideology_color",
                location,
            )
        )
    elif any(channel < 0 or channel > 255 for channel in ideology.color):
        issues.append(
            _error(
                f"Ideology '{ideology.id}' has a colour channel outside 0..255",
                "invalid_ideology_color",
                location,
            )
        )
    subtype_ids = [subtype.name for subtype in ideology.types]
    for subtype_id in subtype_ids:
        if not _SCRIPT_ID.fullmatch(subtype_id):
            issues.append(
                _error(
                    f"Ideology '{ideology.id}' has invalid subtype ID '{subtype_id}'",
                    "invalid_subideology_id",
                    location,
                )
            )
    duplicates = sorted({value for value in subtype_ids if subtype_ids.count(value) > 1})
    for duplicate in duplicates:
        issues.append(
            _error(
                f"Ideology '{ideology.id}' repeats subtype '{duplicate}'",
                "duplicate_subideology_id",
                location,
            )
        )
    if ideology.ai_behavior and ideology.ai_behavior not in VANILLA_AI_BEHAVIORS:
        issues.append(
            _error(
                f"Ideology '{ideology.id}' has unknown AI behavior '{ideology.ai_behavior}'",
                "invalid_ideology_ai_behavior",
                location,
            )
        )
    return issues


def validate_dynamic_idea_group(group: DynamicIdeaGroup) -> list[ValidationError]:
    issues: list[ValidationError] = []
    location = str(group.path) if group.path else None
    if not _SCRIPT_ID.fullmatch(group.name):
        issues.append(
            _error(f"Invalid dynamic idea group name '{group.name}'", "invalid_dynamic_idea_group", location)
        )
    ids = [idea.id for idea in group.ideas]
    for idea in group.ideas:
        if not _SCRIPT_ID.fullmatch(idea.id):
            issues.append(
                _error(f"Invalid dynamic idea ID '{idea.id}'", "invalid_dynamic_idea_id", location)
            )
        for field_name in ("potential", "available"):
            for syntax_issue in validate_script_syntax(getattr(idea, field_name)):
                issues.append(
                    _error(
                        f"Dynamic idea '{idea.id}' {field_name}: {syntax_issue}",
                        "invalid_dynamic_idea_script",
                        location,
                    )
                )
    for duplicate in sorted({value for value in ids if ids.count(value) > 1}):
        issues.append(
            _error(
                f"Dynamic idea ID '{duplicate}' is defined more than once",
                "duplicate_dynamic_idea_id",
                location,
            )
        )
    return issues


def validate_bookmark(bookmark: Bookmark) -> list[ValidationError]:
    issues: list[ValidationError] = []
    location = str(bookmark.path) if bookmark.path else None
    date_error = _validate_date(bookmark.date)
    if date_error:
        issues.append(
            _error(
                f"Bookmark '{bookmark.name}' has invalid date '{bookmark.date}': {date_error}",
                "invalid_bookmark_date",
                location,
            )
        )
    effect_issues = validate_script_syntax(bookmark.effect)
    for syntax_issue in effect_issues:
        issues.append(
            _error(
                f"Bookmark '{bookmark.name}' effect: {syntax_issue}",
                "invalid_bookmark_effect",
                location,
            )
        )
    weather_effect_found = False
    if not effect_issues:
        try:
            weather_effect_found = any(
                span.key == "randomize_weather" and not span.is_block
                for span in top_level_assignments(bookmark.effect)
            )
        except ParseError:
            # The structural syntax error is already reported above in normal
            # cases; malformed assignment shapes still need a useful result.
            pass
    if not weather_effect_found:
        issues.append(
            _error(
                f"Bookmark '{bookmark.name}' must set randomize_weather in its effect block",
                "bookmark_randomize_weather_missing",
                location,
            )
        )
    tags = [country.tag for country in bookmark.countries]
    for tag in tags:
        if not is_bookmark_country_key(tag):
            issues.append(
                _error(
                    f"Bookmark '{bookmark.name}' has invalid country tag '{tag}'",
                    "invalid_bookmark_country_tag",
                    location,
                )
            )
    if bookmark.default_country and bookmark.default_country not in tags:
        issues.append(
            ValidationError(
                message=(
                    f"Bookmark '{bookmark.name}' defaults to '{bookmark.default_country}', "
                    "which has no country entry"
                ),
                severity="warning",
                code="bookmark_default_country_missing",
                file_path=location,
            )
        )
    variants: set[tuple[str, tuple[str, ...], str]] = set()
    for country in bookmark.countries:
        try:
            required_dlc = normalize_required_dlc(country.required_dlc)
        except ValueError as error:
            issues.append(
                _error(
                    f"Bookmark country '{country.tag}' required DLC list: {error}",
                    "invalid_bookmark_required_dlc",
                    location,
                )
            )
            required_dlc = []
        identity = (
            country.tag,
            tuple(required_dlc),
            country.available.strip(),
        )
        if identity in variants:
            issues.append(
                ValidationError(
                    message=(
                        f"Bookmark '{bookmark.name}' repeats an indistinguishable "
                        f"'{country.tag}' country variant"
                    ),
                    severity="warning",
                    code="duplicate_bookmark_country_variant",
                    file_path=location,
                )
            )
        variants.add(identity)
        for syntax_issue in validate_script_syntax(country.available):
            issues.append(
                _error(
                    f"Bookmark country '{country.tag}' availability: {syntax_issue}",
                    "invalid_bookmark_available",
                    location,
                )
            )
    return issues


def _validate_date(value: str) -> str | None:
    try:
        require_bookmark_date(value)
    except ValueError as error:
        return str(error)
    return None


def _error(message: str, code: str, file_path: str | None) -> ValidationError:
    return ValidationError(message=message, severity="error", code=code, file_path=file_path)
