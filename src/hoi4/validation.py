"""
Validation rules for HOI4 mod data.
"""

from __future__ import annotations

import re

from .types import Country, Event, FocusTree, Idea, State, ValidationError

TAG_RE = re.compile(r"^[A-Z0-9]{3}$")


def validate_country(country: Country) -> list[ValidationError]:
    errors: list[ValidationError] = []

    if not TAG_RE.match(country.tag):
        errors.append(ValidationError(
            message=f"Invalid country tag '{country.tag}' (must be 3 alphanumeric characters)",
            severity="error",
            country_tag=country.tag,
        ))

    if not country.name:
        errors.append(ValidationError(
            message=f"Country '{country.tag}' has no name",
            severity="warning",
            country_tag=country.tag,
        ))

    total_pop = sum(country.popularities.values())
    if total_pop > 0 and total_pop != 100:
        errors.append(ValidationError(
            message=f"Country '{country.tag}' popularities sum to {total_pop}, expected 100",
            severity="warning",
            country_tag=country.tag,
        ))

    valid_parties = {"democratic", "fascism", "communism", "neutrality"}
    if country.ruling_party not in valid_parties:
        errors.append(ValidationError(
            message=f"Country '{country.tag}' has invalid ruling party '{country.ruling_party}'",
            severity="error",
            country_tag=country.tag,
        ))

    if country.leader and country.leader.ideology:
        ideology_party_map = {
            "liberalism": "democratic",
            "conservatism": "democratic",
            "socialism": "democratic",
            "marxism": "communism",
            "leninism": "communism",
            "stalinism": "communism",
            "anti_revisionism": "communism",
            "anarchist_communism": "communism",
            "nazism": "fascism",
            "fascism_ideology": "fascism",
            "falangism": "fascism",
            "rexism": "fascism",
            "despotism": "neutrality",
            "oligarchism": "neutrality",
            "moderate": "neutrality",
            "centrism": "neutrality",
        }
        expected_party = ideology_party_map.get(country.leader.ideology)
        if expected_party and country.ruling_party in valid_parties and expected_party != country.ruling_party:
            errors.append(ValidationError(
                message=(
                    f"Country '{country.tag}' ruling party '{country.ruling_party}' does not match "
                    f"leader ideology '{country.leader.ideology}'"
                ),
                severity="warning",
                country_tag=country.tag,
            ))

    try:
        r, g, b = country.color
        if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
            errors.append(ValidationError(
                message=f"Country '{country.tag}' has invalid color {country.color}",
                severity="error",
                country_tag=country.tag,
            ))
    except (TypeError, ValueError):
        errors.append(ValidationError(
            message=f"Country '{country.tag}' has malformed color {country.color!r}",
            severity="error",
            country_tag=country.tag,
        ))

    return errors


def validate_focus_tree(
    tree: FocusTree,
    known_focus_ids: set[str] | None = None,
    known_state_ids: set[int] | None = None,
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    local_ids = {f.id for f in tree.focuses}

    seen_ids: set[str] = set()
    seen_positions: dict[tuple[int, int], str] = {}
    valid_filters = {
        "FOCUS_FILTER_POLITICAL",
        "FOCUS_FILTER_RESEARCH",
        "FOCUS_FILTER_INDUSTRY",
        "FOCUS_FILTER_STABILITY",
        "FOCUS_FILTER_WAR_SUPPORT",
        "FOCUS_FILTER_MANPOWER",
        "FOCUS_FILTER_ANNEXATION",
        "FOCUS_FILTER_ARMY_XP",
        "FOCUS_FILTER_NAVY_XP",
        "FOCUS_FILTER_AIR_XP",
        "FOCUS_FILTER_INTERNATIONAL_TRADE",
        "FOCUS_FILTER_DIPLOMACY",
        "FOCUS_FILTER_EXCLUSIONS",
        "FOCUS_FILTER_HISTORICAL",
    }

    for focus in tree.focuses:
        if not focus.id:
            errors.append(ValidationError(
                message="Focus has no ID",
                severity="error",
                file_path=str(tree.path) if tree.path else None,
            ))
            continue

        if focus.id in seen_ids:
            errors.append(ValidationError(
                message=f"Duplicate focus ID '{focus.id}'",
                severity="error",
                focus_id=focus.id,
                file_path=str(tree.path) if tree.path else None,
            ))
        seen_ids.add(focus.id)

        pos = (focus.x, focus.y)
        if pos in seen_positions:
            errors.append(ValidationError(
                message=(
                    f"Focus '{focus.id}' shares position ({focus.x}, {focus.y}) "
                    f"with '{seen_positions[pos]}'"
                ),
                severity="warning",
                focus_id=focus.id,
                file_path=str(tree.path) if tree.path else None,
            ))
        else:
            seen_positions[pos] = focus.id

        all_known = local_ids | (known_focus_ids or set())
        for group in focus.prerequisites:
            for ref in group:
                if ref not in all_known:
                    errors.append(ValidationError(
                        message=f"Focus '{focus.id}' prerequisite '{ref}' not found",
                        severity="error",
                        focus_id=focus.id,
                        file_path=str(tree.path) if tree.path else None,
                    ))

        for group in focus.mutually_exclusive:
            for ref in group:
                if ref not in all_known:
                    errors.append(ValidationError(
                        message=f"Focus '{focus.id}' mutually_exclusive '{ref}' not found",
                        severity="error",
                        focus_id=focus.id,
                        file_path=str(tree.path) if tree.path else None,
                    ))

        for focus_filter in focus.search_filters:
            if focus_filter not in valid_filters:
                errors.append(ValidationError(
                    message=f"Focus '{focus.id}' has unknown search filter '{focus_filter}'",
                    severity="warning",
                    focus_id=focus.id,
                    file_path=str(tree.path) if tree.path else None,
                ))

        if known_state_ids is not None:
            script = "\n".join([
                focus.completion_reward,
                focus.available,
                focus.bypass,
                focus.select_effect,
                focus.complete_tooltip,
                focus.allow_branch,
            ])
            for raw_id in re.findall(r"(?m)^\s*(\d+)\s*=\s*\{", script):
                sid = int(raw_id)
                if sid not in known_state_ids:
                    errors.append(ValidationError(
                        message=f"Focus '{focus.id}' references unknown state ID {sid}",
                        severity="warning",
                        focus_id=focus.id,
                        state_id=sid,
                        file_path=str(tree.path) if tree.path else None,
                    ))

    return errors


def validate_state(
    state: State,
    known_tags: set[str] | None = None,
) -> list[ValidationError]:
    errors: list[ValidationError] = []

    if not state.id:
        errors.append(ValidationError(
            message="State has no ID",
            severity="error",
            state_id=state.id,
        ))

    if not state.owner and known_tags is not None:
        errors.append(ValidationError(
            message=f"State {state.id} has no owner",
            severity="warning",
            state_id=state.id,
        ))

    if state.owner and known_tags and state.owner not in known_tags:
        errors.append(ValidationError(
            message=f"State {state.id} owner '{state.owner}' is not a known country tag",
            severity="warning",
            state_id=state.id,
        ))

    for core in state.cores:
        if known_tags and core not in known_tags:
            errors.append(ValidationError(
                message=f"State {state.id} core '{core}' is not a known country tag",
                severity="warning",
                state_id=state.id,
            ))

    return errors


def validate_event(event: Event) -> list[ValidationError]:
    errors: list[ValidationError] = []

    if not event.id:
        errors.append(ValidationError(
            message="Event has no ID",
            severity="error",
            event_id=event.id,
        ))
        return errors

    if not event.title:
        errors.append(ValidationError(
            message=f"Event '{event.id}' has no title",
            severity="warning",
            event_id=event.id,
        ))

    if not event.description:
        errors.append(ValidationError(
            message=f"Event '{event.id}' has no description",
            severity="warning",
            event_id=event.id,
        ))

    if not event.options:
        errors.append(ValidationError(
            message=f"Event '{event.id}' has no options",
            severity="error",
            event_id=event.id,
        ))

    if event.event_type not in ("country_event", "state_event", "news_event"):
        errors.append(ValidationError(
            message=f"Event '{event.id}' has invalid type '{event.event_type}'",
            severity="error",
            event_id=event.id,
        ))

    return errors


def validate_idea(idea: Idea) -> list[ValidationError]:
    errors: list[ValidationError] = []

    if not idea.id:
        errors.append(ValidationError(
            message="Idea has no ID",
            severity="error",
        ))
        return errors

    if not idea.modifier:
        errors.append(ValidationError(
            message=f"Idea '{idea.id}' has no modifiers",
            severity="warning",
            idea_id=idea.id,
        ))

    return errors
