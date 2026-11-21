"""Script vocabulary, reference and localisation validation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Sequence, cast

from ..characters import (
    CharacterRole,
)
from ..localisation import (
    DEFAULT_LOCALISATION_LANGUAGE,
)
from ..parser import (
    ParseError,
    iter_assignment_blocks,
    strip_comments,
)
from ..patching import (
    AssignmentSpan,
    top_level_assignments,
)
from ..script_vocabulary import (
    GameScriptVocabulary,
    ScriptSource,
    load_game_script_vocabulary,
    validate_script_sources,
)
from ..states import (
    find_state_file,
    read_state,
)
from ..tags import (
    load_all_tags,
)
from ..textio import read_text
from ..types import (
    Event,
    EventOption,
    State,
    ValidationError,
)
from ..validation import (
    validate_event,
)
from ._catalogs import CatalogsLayer
from ._shared import (
    _EQUIPMENT_STOCKPILE_RE,
    _EVENT_REF_RE,
    _HAS_IDEA_RE,
    _IDEA_EFFECT_RE,
    _LOAD_FOCUS_TREE_RE,
    _TECH_BLOCK_RE,
    _filter_validation_errors,
)
from ._states import StatesLayer


def _idea_mutation_ids(
    script: str,
    *,
    ignore_guarded_additions: bool = False,
) -> set[str]:
    """Return ideas mutated by script, optionally ignoring safe guarded adds."""

    mutations: set[str] = set()

    def scalar(container: str, span: AssignmentSpan) -> str:
        return container[span.value_start : span.value_end].strip().strip('"')

    def block(container: str, span: AssignmentSpan) -> str:
        if span.body_start is None or span.body_end is None:
            return ""
        return container[span.body_start : span.body_end]

    def values(container: str, span: AssignmentSpan) -> set[str]:
        if not span.is_block:
            value = scalar(container, span)
            return {value} if value else set()
        return {
            value
            for value in re.findall(
                r'(?<![A-Za-z0-9_.:-])"?([A-Za-z0-9_.:-]+)"?',
                strip_comments(block(container, span)),
            )
            if value.lower() not in {"yes", "no"}
        }

    def negative_has_idea_guards(body: str) -> set[str]:
        guarded: set[str] = set()

        def visit(fragment: str, positive: bool = True) -> None:
            try:
                spans = top_level_assignments(fragment)
            except (ParseError, ValueError):
                return
            for span in spans:
                if span.key == "has_idea" and not positive and not span.is_block:
                    idea_id = scalar(fragment, span)
                    if idea_id:
                        guarded.add(idea_id)
                    continue
                if not span.is_block:
                    continue
                nested = block(fragment, span)
                if span.key == "NOT":
                    visit(nested, not positive)
                elif span.key == "AND" and positive:
                    visit(nested, positive)
                elif span.key == "OR" and not positive:
                    visit(nested, positive)

        visit(body)
        return guarded

    def process(
        container: str,
        span: AssignmentSpan,
        guarded_absent: set[str],
    ) -> None:
        key = span.key
        if key in {"add_ideas", "remove_ideas"}:
            for idea_id in values(container, span):
                if (
                    key == "add_ideas"
                    and ignore_guarded_additions
                    and idea_id in guarded_absent
                ):
                    continue
                mutations.add(idea_id)
            return
        if key == "add_timed_idea" and span.is_block:
            try:
                timed_spans = top_level_assignments(block(container, span))
            except (ParseError, ValueError):
                timed_spans = []
            timed_body = block(container, span)
            for timed_span in timed_spans:
                if timed_span.key != "idea" or timed_span.is_block:
                    continue
                idea_id = scalar(timed_body, timed_span)
                if not (
                    ignore_guarded_additions and idea_id in guarded_absent
                ):
                    mutations.add(idea_id)
            return
        if key == "swap_ideas" and span.is_block:
            swap_body = block(container, span)
            try:
                swap_spans = top_level_assignments(swap_body)
            except (ParseError, ValueError):
                swap_spans = []
            for swap_span in swap_spans:
                if (
                    swap_span.key in {"add_idea", "remove_idea"}
                    and not swap_span.is_block
                ):
                    mutations.add(scalar(swap_body, swap_span))
            return
        if key in {"if", "else_if"} and span.is_block:
            walk_if(block(container, span), guarded_absent)
            return
        if span.is_block:
            walk(block(container, span), guarded_absent)

    def walk_if(body: str, inherited_guards: set[str]) -> None:
        try:
            spans = top_level_assignments(body)
        except (ParseError, ValueError):
            return
        local_guards = set(inherited_guards)
        for span in spans:
            if span.key == "limit" and span.is_block:
                local_guards.update(negative_has_idea_guards(block(body, span)))
        for span in spans:
            if span.key == "limit":
                continue
            if span.key == "else" and span.is_block:
                walk(block(body, span), inherited_guards)
                continue
            process(body, span, local_guards)

    def walk(body: str, guarded_absent: set[str]) -> None:
        try:
            spans = top_level_assignments(body)
        except (ParseError, ValueError):
            return
        for span in spans:
            process(body, span, guarded_absent)

    walk(script, set())
    return {idea_id for idea_id in mutations if idea_id}


class ScriptValidationLayer(StatesLayer, CatalogsLayer):
    """Script vocabulary, reference and localisation validation."""

    def validate_effect(
        self,
        script: str,
        suppress_warnings: list[str] | tuple[str, ...] | set[str] | None = None,
        *,
        script_token_allowlist: Sequence[str] = (),
        scope: str | None = "COUNTRY",
    ) -> list[ValidationError]:
        known_tags = set(load_all_tags(self._base_content_root, self.mod_root))
        known_tags.update(self._countries.keys())
        known_ideas = (
            self._known_idea_ids()
            if (_IDEA_EFFECT_RE.search(script) or _HAS_IDEA_RE.search(script))
            else set(self._ideas)
        )
        known_events = (
            self._known_event_ids() if _EVENT_REF_RE.search(script) else set(self._events)
        )
        known_technologies = (
            self._known_technology_ids() if _TECH_BLOCK_RE.search(script) else set()
        )
        known_equipment = (
            self._known_equipment_ids() if _EQUIPMENT_STOCKPILE_RE.search(script) else set()
        )
        known_focus_trees = set(self._focus_trees)
        probe = Event(
            id="effect_probe.1",
            title="Effect Probe",
            description="Effect Probe",
            options=[
                EventOption(name="effect_probe.1.a", effect=script),
            ],
        )
        errors = validate_event(probe, namespace="effect_probe", known_tags=known_tags)
        entries: list[tuple[str, str, str | None, str | None]] = [
            (script, "effect", "effect_probe", None)
        ]
        errors.extend(
            self._validate_script_references(
                known_ideas=known_ideas,
                known_events=known_events,
                known_technologies=known_technologies,
                known_equipment=known_equipment,
                known_focus_trees=known_focus_trees,
                entries=entries,
            )
        )
        errors.extend(self._validate_state_effect_assumptions(entries=entries))
        if self.hoi4_install is not None:
            errors.extend(
                validate_script_sources(
                    [
                        ScriptSource(
                            script,
                            "effect",
                            "effect",
                            "effect_probe",
                            scope=scope,
                        )
                    ],
                    self.game_script_vocabulary(),
                    mod_root=self.mod_root,
                    custom_tokens=self._effective_custom_script_tokens(),
                    allowlist=script_token_allowlist,
                )
            )
        return _filter_validation_errors(
            errors,
            suppress_warnings=suppress_warnings,
        )

    def game_script_vocabulary(self) -> GameScriptVocabulary:
        """Return documented script tokens and installed-game usage counts."""

        if self.hoi4_install is None:
            raise ValueError(
                "game_script_vocabulary() requires a configured HOI4 installation"
            )
        if self._script_vocabulary_cache is None:
            self._script_vocabulary_cache = load_game_script_vocabulary(
                self.hoi4_install,
                ideology_ids=self._ideologies,
            )
        return self._script_vocabulary_cache

    def validate_script_vocabulary(
        self,
        *,
        allowlist: Sequence[str] = (),
    ) -> list[ValidationError]:
        """Warn about undocumented effect, trigger, and modifier tokens."""

        return validate_script_sources(
            self._semantic_script_sources(),
            self.game_script_vocabulary(),
            mod_root=self.mod_root,
            custom_tokens=self._effective_custom_script_tokens(),
            allowlist=allowlist,
        )

    def _semantic_script_sources(self) -> list[ScriptSource]:
        sources: list[ScriptSource] = []

        def add(
            text: str,
            kind: Literal["effect", "trigger", "modifier"],
            owner_kind: str,
            owner_id: str,
            path: Path | None,
            scope: str | None = None,
        ) -> None:
            if text.strip():
                sources.append(
                    ScriptSource(
                        text=text,
                        kind=kind,
                        owner_kind=owner_kind,
                        owner_id=owner_id,
                        file_path=str(path) if path is not None else None,
                        scope=scope,
                    )
                )

        for tree in self._focus_trees.values():
            for focus in tree.focuses:
                for text in (
                    focus.completion_reward,
                    focus.select_effect,
                    focus.complete_tooltip,
                ):
                    add(
                        text,
                        "effect",
                        "focus",
                        focus.id,
                        tree.path,
                        scope="COUNTRY",
                    )
                for text in (focus.available, focus.bypass, focus.allow_branch):
                    add(
                        text,
                        "trigger",
                        "focus",
                        focus.id,
                        tree.path,
                        scope="COUNTRY",
                    )
        for event in self._events.values():
            event_scope = {
                "state_event": "STATE",
                "unit_leader_event": "CHARACTER",
                "operative_leader_event": "CHARACTER",
            }.get(event.event_type, "COUNTRY")
            add(
                event.trigger,
                "trigger",
                "event",
                event.id,
                event.path,
                scope=event_scope,
            )
            add(
                event.immediate,
                "effect",
                "event",
                event.id,
                event.path,
                scope=event_scope,
            )
            for option in event.options:
                add(
                    option.trigger,
                    "trigger",
                    "event",
                    event.id,
                    event.path,
                    scope=event_scope,
                )
                add(
                    option.effect,
                    "effect",
                    "event",
                    event.id,
                    event.path,
                    scope=event_scope,
                )
        for occurrences in self._on_action_occurrences.values():
            for action in occurrences:
                add(action.effect, "effect", "on_action", action.id, action.path)
                if action.events or action.random_events:
                    add(
                        "\n".join(
                            f"country_event = {{ id = {event_id} }}"
                            for event_id in (*action.events, *action.random_events)
                        ),
                        "effect",
                        "on_action",
                        action.id,
                        action.path,
                    )
        for decision in self._decisions.values():
            add(
                decision.available,
                "trigger",
                "decision",
                decision.id,
                decision.path,
                scope="COUNTRY",
            )
            add(
                decision.visible,
                "trigger",
                "decision",
                decision.id,
                decision.path,
                scope="COUNTRY",
            )
            add(
                decision.complete_effect,
                "effect",
                "decision",
                decision.id,
                decision.path,
                scope="COUNTRY",
            )
            add(
                decision.remove_effect,
                "effect",
                "decision",
                decision.id,
                decision.path,
                scope="COUNTRY",
            )
            # Mission and targeted-decision fields run in the decision
            # owner's (country) scope; targets are reached through FROM.
            for trigger_field in (
                "allowed",
                "activation",
                "cancel_trigger",
                "remove_trigger",
                "target_trigger",
                "target_root_trigger",
                "custom_cost_trigger",
            ):
                add(
                    getattr(decision, trigger_field),
                    "trigger",
                    "decision",
                    decision.id,
                    decision.path,
                    scope="COUNTRY",
                )
            for effect_field in ("timeout_effect", "cancel_effect"):
                add(
                    getattr(decision, effect_field),
                    "effect",
                    "decision",
                    decision.id,
                    decision.path,
                    scope="COUNTRY",
                )
            add(
                decision.modifier,
                "modifier",
                "decision",
                decision.id,
                decision.path,
            )
        for idea in self._ideas.values():
            add(
                idea.allowed,
                "trigger",
                "idea",
                idea.id,
                idea.path,
                scope="COUNTRY",
            )
            if idea.modifier:
                add(
                    "\n".join(f"{key} = 0" for key in idea.modifier),
                    "modifier",
                    "idea",
                    idea.id,
                    idea.path,
                )
        for modifier in self._dynamic_modifiers.values():
            add(
                modifier.enable,
                "trigger",
                "dynamic_modifier",
                modifier.id,
                modifier.path,
            )
            add(
                modifier.remove_trigger,
                "trigger",
                "dynamic_modifier",
                modifier.id,
                modifier.path,
            )
            if modifier.modifier:
                add(
                    "\n".join(f"{key} = 0" for key in modifier.modifier),
                    "modifier",
                    "dynamic_modifier",
                    modifier.id,
                    modifier.path,
                )
        for bookmark in self._bookmarks:
            add(
                bookmark.effect,
                "effect",
                "bookmark",
                bookmark.name,
                bookmark.path,
                scope="COUNTRY",
            )
            for country in bookmark.countries:
                add(
                    country.available,
                    "trigger",
                    "bookmark",
                    bookmark.name,
                    bookmark.path,
                    scope="COUNTRY",
                )
        for character in self._characters.values():
            scopes: list[tuple[list[CharacterRole], str | None]] = [
                (character.roles, None)
            ]
            scopes.extend((instance.roles, instance.allowed) for instance in character.instances)
            for roles, allowed in scopes:
                add(
                    allowed or "",
                    "trigger",
                    "character",
                    character.id,
                    character.path,
                    scope="COUNTRY",
                )
                for role in roles:
                    for attribute in ("allowed", "visible", "available"):
                        add(
                            str(getattr(role, attribute, "")),
                            "trigger",
                            "character",
                            character.id,
                            character.path,
                            scope="COUNTRY",
                        )
        for kind, relative in (
            ("effect", Path("common/scripted_effects")),
            ("trigger", Path("common/scripted_triggers")),
        ):
            directory = self.mod_root / relative
            paths = set(directory.rglob("*.txt"))
            paths.update(path for path in self._script_file_updates if path.is_relative_to(directory))
            for path in sorted(paths):
                try:
                    pending_text = self._script_file_updates.get(path)
                    text = (pending_text if pending_text is not None else
                            read_text(path))
                    for span in top_level_assignments(text):
                        if (
                            span.is_block
                            and span.body_start is not None
                            and span.body_end is not None
                        ):
                            add(
                                text[span.body_start : span.body_end],
                                cast(
                                    Literal["effect", "trigger", "modifier"],
                                    kind,
                                ),
                                f"scripted_{kind}",
                                span.key,
                                path,
                            )
                except (OSError, ValueError):
                    continue
        return sources

    def _script_entries(self) -> list[tuple[str, str, str | None, str | None]]:
        entries: list[tuple[str, str, str | None, str | None]] = []
        for tree in self._focus_trees.values():
            for focus in tree.focuses:
                script = "\n".join(
                    [
                        focus.completion_reward,
                        focus.available,
                        focus.bypass,
                        focus.select_effect,
                        focus.complete_tooltip,
                        focus.allow_branch,
                    ]
                )
                entries.append((script, "focus", focus.id, str(tree.path) if tree.path else None))
        for event in self._events.values():
            scripts = [event.trigger, event.immediate, event.mean_time_to_happen]
            scripts.extend(option.trigger + "\n" + option.effect for option in event.options)
            entries.append(
                ("\n".join(scripts), "event", event.id, str(event.path) if event.path else None)
            )
        for occurrences in self._on_action_occurrences.values():
            for action in occurrences:
                entries.append(
                    (
                        action.effect,
                        "on_action",
                        action.id,
                        str(action.path) if action.path else None,
                    )
                )
                if action.events:
                    entries.append(
                        (
                            "\n".join(
                                f"country_event = {{ id = {event_id} }}"
                                for event_id in action.events
                            ),
                            "on_action",
                            action.id,
                            str(action.path) if action.path else None,
                        )
                    )
        for decision in self._decisions.values():
            entries.append(
                (
                    "\n".join(
                        [
                            decision.available,
                            decision.visible,
                            decision.complete_effect,
                            decision.remove_effect,
                            decision.allowed,
                            decision.activation,
                            decision.cancel_trigger,
                            decision.remove_trigger,
                            decision.target_trigger,
                            decision.target_root_trigger,
                            decision.custom_cost_trigger,
                            decision.timeout_effect,
                            decision.cancel_effect,
                        ]
                    ),
                    "decision",
                    decision.id,
                    str(decision.path) if decision.path else None,
                )
            )
        return entries

    def _validate_script_references(
        self,
        *,
        known_ideas: set[str],
        known_events: set[str],
        known_technologies: set[str],
        known_equipment: set[str],
        known_focus_trees: set[str],
        entries: list[tuple[str, str, str | None, str | None]] | None = None,
    ) -> list[ValidationError]:
        errors: list[ValidationError] = []
        script_entries = entries or self._script_entries()
        known_state_ids = (
            {entry["id"] for entry in self.state_index()}
            if any("start_civil_war" in script for script, _, _, _ in script_entries)
            else set()
        )
        runtime_entries = list(script_entries)
        if entries is None:
            runtime_entries.extend(
                (
                    source.text,
                    source.owner_kind,
                    source.owner_id,
                    source.file_path,
                )
                for source in self._semantic_script_sources()
                if source.owner_kind == "scripted_effect"
            )
        for script, kind, obj_id, file_path in runtime_entries:
            if not script:
                continue
            matches = list(
                re.finditer(
                    r"\brecruit_character\s*=",
                    self._mask_script_comments(script),
                )
            )
            for occurrence, match in enumerate(matches):
                line, column = self._script_reference_location(
                    script,
                    match.start(),
                    obj_id=obj_id,
                    file_path=file_path,
                    token="recruit_character",
                    occurrence=occurrence,
                )
                errors.append(
                    self._script_ref_error(
                        (
                            f"{kind} '{obj_id}' uses recruit_character at runtime. "
                            "HOI4 only executes recruit_character from game/history "
                            "at scenario start; recruit the character in country "
                            "history and gate its role availability instead."
                        ),
                        "runtime_recruit_character",
                        kind,
                        obj_id,
                        file_path,
                        severity="error",
                        line=line,
                        column=column,
                    )
                )
        for script, kind, obj_id, file_path in script_entries:
            if not script:
                continue
            for idea_id in sorted(
                set(_IDEA_EFFECT_RE.findall(script) + _HAS_IDEA_RE.findall(script))
            ):
                if idea_id not in known_ideas:
                    errors.append(
                        self._script_ref_error(
                            f"{kind} '{obj_id}' references unknown idea '{idea_id}'",
                            "unknown_idea_reference",
                            kind,
                            obj_id,
                            file_path,
                            idea_id=idea_id,
                        )
                    )
                elif re.search(rf"\badd_ideas\s*=\s*{re.escape(idea_id)}\b", script):
                    idea = self._ideas.get(idea_id)
                    if idea is not None and idea.category != "country":
                        errors.append(
                            self._script_ref_error(
                                f"{kind} '{obj_id}' adds idea '{idea_id}', but its category is '{idea.category or '<none>'}', not 'country'",
                                "idea_not_addable",
                                kind,
                                obj_id,
                                file_path,
                                idea_id=idea_id,
                            )
                        )
            for event_id in sorted(set(_EVENT_REF_RE.findall(script))):
                if known_events and event_id not in known_events:
                    errors.append(
                        self._script_ref_error(
                            f"{kind} '{obj_id}' references unknown event '{event_id}'",
                            "unknown_event_reference",
                            kind,
                            obj_id,
                            file_path,
                            event_id=event_id,
                        )
                    )
            for tree_id in sorted(set(_LOAD_FOCUS_TREE_RE.findall(script))):
                if tree_id not in known_focus_trees:
                    errors.append(
                        self._script_ref_error(
                            f"{kind} '{obj_id}' references unknown focus tree '{tree_id}'",
                            "unknown_focus_tree_reference",
                            kind,
                            obj_id,
                            file_path,
                        )
                    )
            if known_state_ids:
                for body, _, _ in iter_assignment_blocks(script, "start_civil_war"):
                    capital_match = re.search(r"\bcapital\s*=\s*(\d+)\b", body)
                    if (
                        capital_match is not None
                        and int(capital_match.group(1)) not in known_state_ids
                    ):
                        errors.append(
                            self._script_ref_error(
                                (
                                    f"{kind} '{obj_id}' uses civil-war capital "
                                    f"{capital_match.group(1)}, which is not a known state ID"
                                ),
                                "civil_war_capital_ref",
                                kind,
                                obj_id,
                                file_path,
                            )
                        )
            if known_technologies:
                for tech_id in sorted(set(self._technology_refs(script))):
                    if tech_id not in known_technologies:
                        errors.append(
                            self._script_ref_error(
                                f"{kind} '{obj_id}' references unknown technology '{tech_id}'",
                                "unknown_technology_reference",
                                kind,
                                obj_id,
                                file_path,
                            )
                        )
            if known_equipment:
                for equipment_id in sorted(set(self._equipment_refs(script))):
                    if equipment_id not in known_equipment:
                        errors.append(
                            self._script_ref_error(
                                f"{kind} '{obj_id}' references unknown equipment '{equipment_id}'",
                                "unknown_equipment_reference",
                                kind,
                                obj_id,
                                file_path,
                            )
                        )
            remove_count = len(re.findall(r"\bremove_ideas\s*=", script))
            add_count = len(re.findall(r"\badd_ideas\s*=", script))
            if remove_count >= 2 and add_count >= 1 and "swap_ideas" not in script:
                errors.append(
                    self._script_ref_error(
                        f"{kind} '{obj_id}' removes {remove_count} ideas and adds {add_count}; consider Mod.effect_swap_idea() or hidden effects for cleaner tooltips",
                        "bad_idea_tooltip_pattern",
                        kind,
                        obj_id,
                        file_path,
                    )
                )
        return errors

    def _validate_state_effect_assumptions(
        self,
        *,
        entries: list[tuple[str, str, str | None, str | None]] | None = None,
    ) -> list[ValidationError]:
        errors: list[ValidationError] = []
        for script, kind, obj_id, file_path in entries or self._script_entries():
            if not script:
                continue
            for state_id_text in sorted(
                set(
                    re.findall(
                        r"\b([0-9]+)\s*=\s*\{[^{}]*\badd_resistance\s*=",
                        script,
                        flags=re.DOTALL,
                    )
                )
            ):
                state = self._read_state_readonly(int(state_id_text))
                if state and state.owner and state.owner in state.cores:
                    errors.append(
                        self._script_ref_error(
                            (
                                f"{kind} '{obj_id}' adds resistance to state {state.id}, but current owner "
                                f"{state.owner} is already a core. Occupation resistance will not behave like "
                                "generic unrest; use flags, variables, decisions, or revolt events."
                            ),
                            "resistance_on_core_state",
                            kind,
                            obj_id,
                            file_path,
                        )
                    )
            for tag, state_id_text in sorted(
                set(
                    re.findall(
                        r"\b([A-Z][A-Z0-9]{2})\s*=\s*\{[^{}]*\btransfer_state\s*=\s*([0-9]+)\b",
                        script,
                        flags=re.DOTALL,
                    )
                )
            ):
                state = self._read_state_readonly(int(state_id_text))
                if state and state.owner == tag:
                    errors.append(
                        self._script_ref_error(
                            (
                                f"{kind} '{obj_id}' transfers state {state.id} to {tag}, but {tag} already "
                                "owns that state in loaded history. Check released/existing-country edge cases."
                            ),
                            "revolt_state_already_owned",
                            kind,
                            obj_id,
                            file_path,
                        )
                    )
        return errors

    def _read_state_readonly(self, state_id: int) -> State | None:
        if state_id in self._states:
            return self._states[state_id]
        for base in (self.mod_root, self._base_content_root):
            if base is None:
                continue
            path = find_state_file(base / "history" / "states", state_id)
            if path:
                try:
                    return read_state(path)
                except Exception:
                    return None
        return None

    def _validate_localization_references(self) -> list[ValidationError]:
        errors: list[ValidationError] = []

        def add_missing(
            message: str,
            *,
            focus_id: str | None = None,
            event_id: str | None = None,
            idea_id: str | None = None,
            country_tag: str | None = None,
            file_path: str | None = None,
        ) -> None:
            errors.append(
                ValidationError(
                    message=message,
                    severity="warning",
                    code="missing_localization",
                    focus_id=focus_id,
                    event_id=event_id,
                    idea_id=idea_id,
                    country_tag=country_tag,
                    file_path=file_path,
                )
            )

        for event in self._events.values():
            event_keys = [
                ("title", key)
                for key in (event.title, *(e.text for e in event.conditional_titles))
            ] + [
                ("description", key)
                for key in (event.description, *(e.text for e in event.conditional_descriptions))
            ] + [
                ("option", key)
                for option in event.options
                for key in (option.name, *(e.text for e in option.conditional_names))
            ]
            for label, key in event_keys:
                if key and not self._has_loc(key):
                    add_missing(
                        f"Event '{event.id}' {label} localization '{key}' not found",
                        event_id=event.id,
                        file_path=str(event.path) if event.path else None,
                    )

        for idea in self._ideas.values():
            if not self._has_loc(idea.id):
                add_missing(
                    f"Idea '{idea.id}' localization not found",
                    idea_id=idea.id,
                    file_path=str(idea.path) if idea.path else None,
                )
            if not self._has_loc(f"{idea.id}_desc"):
                add_missing(
                    f"Idea '{idea.id}' description localization '{idea.id}_desc' not found",
                    idea_id=idea.id,
                    file_path=str(idea.path) if idea.path else None,
                )

        for country in self._countries.values():
            for key in (country.tag, f"{country.tag}_DEF", f"{country.tag}_ADJ"):
                if not self._has_loc(key):
                    add_missing(
                        f"Country '{country.tag}' localization '{key}' not found",
                        country_tag=country.tag,
                    )
            if (
                country.leader
                and country.leader.character_id
                and not self._has_loc(country.leader.character_id)
            ):
                add_missing(
                    f"Country '{country.tag}' leader localization '{country.leader.character_id}' not found",
                    country_tag=country.tag,
                )
        return errors

    def _has_loc(self, key: str) -> bool:
        return key in self._loc_entries or f"{key}:0" in self._loc_entries

    def _validate_localization_languages(self) -> list[ValidationError]:
        """Warn about authored English keys missing from a shipped language.

        Header/folder agreement is a source-tree check; see
        ``_validate_source_tree``. Languages the mod does not ship at all are
        not reported: HOI4 then falls back to raw keys everywhere, which is a
        packaging choice rather than a gap.
        """
        errors: list[ValidationError] = []
        shipped = self._shipped_loc_languages()
        if not shipped:
            return errors
        english_keys = self._authored_loc_keys(DEFAULT_LOCALISATION_LANGUAGE)
        for language in shipped:
            _, entries, _, _ = self._loc_tables(language)
            missing = sorted(key for key in english_keys if key not in entries)
            if not missing:
                continue
            preview = ", ".join(missing[:10]) + (", ..." if len(missing) > 10 else "")
            errors.append(
                ValidationError(
                    message=(
                        f"{len(missing)} English localization key(s) have no {language} "
                        f"translation: {preview}. Players see raw keys; call "
                        f"copy_loc_to_languages(languages=['{language}']) to fill them "
                        "from English"
                    ),
                    severity="warning",
                    code="missing_translation",
                    file_path=str(self.mod_root / "localisation" / language),
                )
            )
        return errors

    def _validate_idea_mutation_collisions(self) -> list[ValidationError]:
        focus_mutations: dict[str, set[str]] = {}
        runtime_mutations: dict[str, set[str]] = {}
        for script, kind, obj_id, _ in self._script_entries():
            ideas = _idea_mutation_ids(
                script,
                ignore_guarded_additions=kind != "focus",
            )
            if not ideas:
                continue
            target = focus_mutations if kind == "focus" else runtime_mutations
            target.setdefault(obj_id or "<unknown>", set()).update(ideas)
        focus_ideas = set().union(*focus_mutations.values()) if focus_mutations else set()
        runtime_ideas = set().union(*runtime_mutations.values()) if runtime_mutations else set()
        collisions = sorted(focus_ideas & runtime_ideas)
        if not collisions:
            return []
        return [
            ValidationError(
                message=(
                    "Focuses and runtime events/decisions mutate the same idea IDs "
                    f"({', '.join(collisions)}). Check delayed events cannot downgrade a staged spirit."
                ),
                severity="warning",
                code="idea_mutation_collision",
            )
        ]

    def _script_ref_error(
        self,
        message: str,
        code: str,
        kind: str,
        obj_id: str | None,
        file_path: str | None,
        *,
        idea_id: str | None = None,
        event_id: str | None = None,
        severity: Literal["error", "warning"] = "warning",
        line: int | None = None,
        column: int | None = None,
    ) -> ValidationError:
        return ValidationError(
            message=message,
            severity=severity,
            code=code,
            file_path=file_path,
            focus_id=obj_id if kind == "focus" else None,
            event_id=event_id or (obj_id if kind == "event" else None),
            idea_id=idea_id,
            line=line,
            column=column,
        )

    @classmethod
    def _script_reference_location(
        cls,
        script: str,
        script_offset: int,
        *,
        obj_id: str | None,
        file_path: str | None,
        token: str,
        occurrence: int = 0,
    ) -> tuple[int, int]:
        """Resolve a script finding to its source file when possible."""

        if file_path is None:
            return cls._line_column(script, script_offset)
        path = Path(file_path)
        try:
            source = read_text(path)
        except OSError:
            return cls._line_column(script, script_offset)

        ranges: list[tuple[int, int]] = []

        def find_object_ranges(fragment: str, base_offset: int) -> None:
            try:
                spans = top_level_assignments(fragment)
            except (ParseError, ValueError):
                return
            for span in spans:
                if (
                    not span.is_block
                    or span.body_start is None
                    or span.body_end is None
                ):
                    continue
                body = fragment[span.body_start : span.body_end]
                start = base_offset + span.body_start
                end = base_offset + span.body_end
                direct_id = next(
                    (
                        item
                        for item in top_level_assignments(body)
                        if item.key == "id" and not item.is_block
                    ),
                    None,
                )
                value = (
                    body[direct_id.value_start : direct_id.value_end]
                    .strip()
                    .strip('"')
                    if direct_id is not None
                    else ""
                )
                if obj_id is not None and (span.key == obj_id or value == obj_id):
                    ranges.append((start, end))
                find_object_ranges(body, start)

        if obj_id is not None:
            find_object_ranges(source, 0)
        masked = cls._mask_script_comments(source)
        pattern = re.compile(rf"\b{re.escape(token)}\s*=")
        for start, end in ranges:
            base = source.find(script, start, end)
            if base >= 0:
                return cls._line_column(source, base + script_offset)
        remaining = occurrence
        for start, end in ranges:
            matches = list(pattern.finditer(masked, start, end))
            if remaining < len(matches):
                return cls._line_column(source, matches[remaining].start())
            remaining -= len(matches)
        if ranges:
            # The modeled object has diverged from its on-disk source. Do not
            # borrow an identically named token from another source object.
            return cls._line_column(script, script_offset)
        base = source.find(script)
        if base >= 0:
            return cls._line_column(source, base + script_offset)
        matches = list(pattern.finditer(masked))
        if occurrence < len(matches):
            return cls._line_column(source, matches[occurrence].start())
        return cls._line_column(script, script_offset)
