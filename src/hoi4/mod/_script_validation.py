"""Script vocabulary, reference and localisation validation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Literal, Sequence

from ..localisation import (
    DEFAULT_LOCALISATION_LANGUAGE,
)
from ..script_references import (
    COUNTRY,
    DYNAMIC_TAG_RE,
    UNKNOWN,
    ScriptOwner,
    ScriptRef,
    dynamic_tags_of,
    event_key,
    fragment_owner,
    is_country_tag,
)
from ..script_vocabulary import (
    GameScriptVocabulary,
    ScriptSource,
    load_game_script_vocabulary,
    script_sources,
    validate_script_sources,
)
from ..states import (
    find_state_file,
    read_state,
)
from ..syntax import Assignment, Block, parse
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
from ._shared import _filter_validation_errors
from ._states import StatesLayer

Severity = Literal["error", "warning", "info"]

# Owners whose script runs during the game, not at scenario start.
_RUNTIME_OWNERS = frozenset(
    {"focus", "event", "on_action", "decision", "decision_category", "scripted_effect",
     "idea", "effect"}
)
# Owners whose blocks are effects and triggers, where a tag key is a scope.
_SCRIPT_OWNERS = frozenset(
    {"focus", "focus_tree", "event", "decision", "decision_category", "on_action",
     "scripted_effect", "scripted_trigger", "idea", "effect"}
)
# Owners whose idea changes are compared: focuses against runtime script.
_MUTATING_OWNERS = frozenset({"focus", "event", "on_action", "decision"})
# References to something by its name.
_NAMED_KINDS = frozenset({"idea", "event", "focus_tree", "technology", "equipment"})
# Country and state history: checked by the history validators.
_HISTORY_OWNERS = frozenset({"country_history", "state_history"})
# Idea categories that fill an advisor slot: hired, never added.
_HIRED_IDEA_CATEGORIES = frozenset(
    {"political_advisor", "army_chief", "navy_chief", "air_chief", "high_command", "theorist"}
)


def _core_effect_message(ref: ScriptRef) -> str:
    if is_country_tag(ref.scope_key) and ref.value.isdigit():
        return (
            f"'{ref.scope_key} = {{ {ref.key} = {ref.value} }}' runs in the country "
            f"{ref.scope_key}, not in a state. Use '{ref.value} = {{ {ref.key} = "
            f"{ref.scope_key} }}' for a state-scoped core change."
        )
    return (
        f"'{ref.key}' appears at country scope. Use a state scope such as "
        f"'115 = {{ {ref.key} = TAG }}' or every_owned_state to change cores."
    )


class _LazyCatalogs:
    """Catalogs of what exists, completed only when a name is not found.

    The caller's sets are tried first. A miss loads the full catalog of the
    mod and the game once, so a mod with no unknown names never pays for it.
    """

    def __init__(
        self,
        mod: ScriptValidationLayer,
        state_ids: Callable[[], set[int]],
        **known: set[str],
    ) -> None:
        self.mod = mod
        self.state_ids = state_ids
        self.known = {name: set(values) for name, values in known.items()}
        self.complete: set[str] = set()
        self.tags: set[str] | None = None
        self.states: set[int] | None = None
        self.folded: dict[str, dict[str, str]] = {}
        # Event ids with folded namespaces, before and after the full catalog.
        self.event_keys: dict[bool, set[str]] = {}

    def _load(self, name: str) -> set[str]:
        values = self.known[name]
        if name in self.complete:
            return values
        self.complete.add(name)
        mod = self.mod
        if name == "ideas":
            values |= mod._known_idea_ids() | mod._known_advisor_idea_ids()
        elif name == "events":
            values |= mod._known_event_ids()
        elif name == "focus_trees":
            values |= mod._known_focus_tree_ids()
        elif name == "technologies":
            values |= mod._known_technology_ids()
        elif name == "equipment":
            values |= mod._known_equipment_ids()
        return values

    def has(self, name: str, value: str) -> bool:
        if value in self.known[name]:
            return True
        if name == "events" and self._has_event(value, complete=False):
            return True
        values = self._load(name)
        if name == "events" and self._has_event(value, complete=True):
            return True
        # Without a catalog (no game install and nothing defined) nothing can
        # be said about an event, a technology or a piece of equipment.
        return value in values or (not values and name != "ideas")

    def _has_event(self, value: str, *, complete: bool) -> bool:
        """The game matches an event's namespace without regard to case."""

        keys = self.event_keys.get(complete)
        if keys is None:
            keys = {event_key(known) for known in self.known["events"]}
            self.event_keys[complete] = keys
        return event_key(value) in keys

    def has_idea(self, value: str, *, show: bool = False) -> bool:
        if self.has("ideas", value):
            return True
        return show and value in self.mod._known_organization_ids()

    def case_hint(self, name: str, value: str) -> str:
        folded = self.folded.get(name)
        if folded is None:
            folded = {known.lower(): known for known in self._load(name)}
            self.folded[name] = folded
        match = folded.get(value.lower())
        if match is None:
            return ""
        return f"; '{match}' exists, but names are case-sensitive"

    def has_tag(self, tag: str) -> bool:
        if self.tags is None:
            self.tags = self.mod._known_country_tags()
        return tag in self.tags

    def has_state(self, state_id: int) -> bool:
        if self.states is None:
            self.states = self.state_ids()
        return not self.states or state_id in self.states


def _idea_mutation_ids(
    script: Block,
    *,
    ignore_guarded_additions: bool = False,
) -> set[str]:
    """Ideas a block of script adds or removes, read from the syntax tree.

    With ``ignore_guarded_additions``, an idea added inside
    ``if = { limit = { NOT = { has_idea = X } } add_ideas = X }`` is left out:
    the guard makes the addition safe to repeat.
    """

    mutations: set[str] = set()

    def absent_guards(block: Block, positive: bool = True) -> set[str]:
        guarded: set[str] = set()
        for item in block.assignments():
            name = item.key.value.lower()
            if name == "has_idea" and not positive and item.scalar:
                guarded.add(item.scalar)
            elif item.block is None:
                continue
            elif name == "not":
                guarded |= absent_guards(item.block, not positive)
            elif (name == "and" and positive) or (name == "or" and not positive):
                guarded |= absent_guards(item.block, positive)
        return guarded

    def idea_values(item: Assignment) -> list[str]:
        if item.scalar is not None:
            return [item.scalar]
        if item.block is None:
            return []
        return [
            value.value
            for value in item.block.values()
            if value.value.lower() not in {"yes", "no"}
        ]

    def walk(block: Block, guarded_absent: set[str]) -> None:
        for item in block.assignments():
            visit(item, guarded_absent)

    def visit(item: Assignment, guarded_absent: set[str]) -> None:
        name = item.key.value.lower()
        inner = item.block
        if name in {"add_ideas", "remove_ideas"}:
            for idea_id in idea_values(item):
                if not (
                    name == "add_ideas"
                    and ignore_guarded_additions
                    and idea_id in guarded_absent
                ):
                    mutations.add(idea_id)
        elif inner is None:
            return
        elif name == "add_timed_idea":
            timed = inner.scalar("idea")
            if timed and not (ignore_guarded_additions and timed in guarded_absent):
                mutations.add(timed)
        elif name == "swap_ideas":
            for field_name in ("add_idea", "remove_idea"):
                swapped = inner.scalar(field_name)
                if swapped:
                    mutations.add(swapped)
        elif name in {"if", "else_if"}:
            local = set(guarded_absent)
            for limit in inner.blocks("limit", fold=True):
                local |= absent_guards(limit)
            for child in inner.assignments():
                child_name = child.key.value.lower()
                if child_name == "limit":
                    continue
                if child_name == "else" and child.block is not None:
                    walk(child.block, guarded_absent)
                else:
                    visit(child, local)
        else:
            walk(inner, guarded_absent)

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
        advice: bool = True,
        suppress: Sequence[str] = (),
    ) -> list[ValidationError]:
        """Check one piece of effect script and return what is wrong with it.

        The script is read as an event option's effect that starts in
        ``scope`` (``None`` for a scope that is not known). It gets the event
        checks, the reference checks (ideas, events, technologies, equipment,
        focus trees, country tags, states) and, with ``hoi4_install`` set, the
        script token check.

        ``advice`` (default ``True``) keeps the ``"info"`` findings, because
        one script is being looked at; pass ``False`` to get only errors and
        warnings, like ``validate()``. ``suppress`` hides warnings and advice
        by code (``"code"`` or ``"code:ID"``), as in ``validate()``;
        ``suppress_warnings`` is its older name. Errors cannot be hidden.
        Every finding has the severity its code is registered with (see
        ``docs/validation.md``).
        """

        probe = Event(
            id="effect_probe.1",
            title="Effect Probe",
            description="Effect Probe",
            options=[
                EventOption(name="effect_probe.1.a", effect=script),
            ],
        )
        errors = validate_event(probe, namespace="effect_probe")
        entries: list[tuple[str, str, str | None, str | None]] = [
            (script, "effect", "effect_probe", None)
        ]
        # The reference check loads a full catalog only when a name is not
        # among the mod's own objects.
        errors.extend(
            self._validate_script_references(
                known_ideas=set(self._ideas),
                known_events=set(self._events),
                known_technologies=set(),
                known_equipment=set(),
                known_focus_trees=set(self._focus_trees),
                entries=entries,
                entry_scope=(scope or UNKNOWN).upper(),
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
            suppress=suppress,
            advice=advice,
        )

    def game_script_vocabulary(self) -> GameScriptVocabulary:
        """Every effect, trigger and modifier name the game and this mod know.

        The install's names (with usage counts) are read once and cached on
        disk; the mod's scripted effects and triggers, ideologies, characters
        and modifier family members are added on every call, so pending edits
        count (a building made with ``create_definition()`` is known before
        ``save()``, a deleted one is gone).
        """

        if self.hoi4_install is None:
            raise ValueError(
                "game_script_vocabulary() requires a configured HOI4 installation"
            )
        if self._script_vocabulary_cache is None:
            # What comes from the install is cached on disk as well.
            self._script_vocabulary_cache = load_game_script_vocabulary(self.hoi4_install)
        custom = self._effective_custom_script_tokens()
        return self._script_vocabulary_cache.extended(
            [*self.base_mod_paths, self.mod_root],
            scripted_effects=custom.get("effect", ()),
            scripted_triggers=custom.get("trigger", ()),
            ideology_ids=self._ideologies,
            characters=self._characters,
            pending=self._pending_script_texts(),
        )

    def validate_script_vocabulary(
        self,
        *,
        allowlist: Sequence[str] = (),
    ) -> list[ValidationError]:
        """Report unknown effect, trigger and modifier names and names in a scope they do not support."""

        return validate_script_sources(
            self._semantic_script_sources(),
            self.game_script_vocabulary(),
            mod_root=self.mod_root,
            custom_tokens=self._effective_custom_script_tokens(),
            allowlist=allowlist,
        )

    def _semantic_script_sources(self) -> list[ScriptSource]:
        """Every effect, trigger, modifier and weight block of the mod's own script files.

        The files are read as ``save()`` will write them, so pending edits
        count. They are split by :mod:`hoi4._script_corpus`, the same split the
        reference check and the corpus test use. Each source carries the
        parsed block (so findings have file lines), the scope it runs in when
        that is certain (``hoi4.script_scopes.ENTRY_SCOPES``) and what
        ``FROM`` is when known. The token check, the idea collision check and
        the liveness check of one validate() run share the list.
        """

        return list(
            self._run_cached("semantic_script_sources", self._build_semantic_script_sources)
        )

    def _build_semantic_script_sources(self) -> list[ScriptSource]:
        sources: list[ScriptSource] = []
        for path, relative, text in self._own_script_texts():
            try:
                root = parse(text).root
            except ValueError:
                continue
            sources.extend(script_sources(root, relative, str(path)))
        return sources

    def _own_script_texts(
        self, tops: tuple[str, ...] = ("common", "events")
    ) -> list[tuple[Path, str, str]]:
        """``(path, relative, text)`` of every script file of the mod itself in ``tops``.

        The text is what the file will hold after ``save()``: pending edits
        count, and a file that ``save()`` deletes is left out.
        """

        pending = self._pending_script_texts()
        paths: dict[str, Path] = {}
        for top in tops:
            folder = self.mod_root / top
            if folder.is_dir():
                for path in folder.rglob("*.txt"):
                    paths[path.relative_to(self.mod_root).as_posix()] = path
        for path in pending:
            if path.is_relative_to(self.mod_root):
                relative = path.relative_to(self.mod_root).as_posix()
                if relative.split("/", 1)[0] in tops and relative.endswith(".txt"):
                    paths[relative] = path
        found: list[tuple[Path, str, str]] = []
        for relative, path in sorted(paths.items()):
            if path in pending:
                text = pending[path]
                if text is None:
                    continue
            else:
                try:
                    text = read_text(path)
                except (OSError, ValueError):
                    continue
            found.append((path, relative, text))
        return found

    def _reference_owners(
        self,
        entries: list[tuple[str, str, str | None, str | None]] | None,
        entry_scope: str = COUNTRY,
    ) -> list[tuple[ScriptOwner, str | None]]:
        """The script to check, owner by owner, with the file it is in.

        Loose ``entries`` (``validate_effect``) are read as effects that start
        in ``entry_scope``. Otherwise every script file of the mod is read, with its
        pending edits; inherited files are the game's and never reported.
        """

        if entries is not None:
            return [
                (fragment_owner(script, kind, obj_id or "", entry_scope), file_path)
                for script, kind, obj_id, file_path in entries
                if script
            ]
        return [
            (owner, found.path)
            for found in self._reference_corpus(inherited=False)
            if found.own
            for owner in found.owners
            if owner.kind not in _HISTORY_OWNERS
        ]

    def _validate_script_references(
        self,
        *,
        known_ideas: set[str],
        known_events: set[str],
        known_technologies: set[str],
        known_equipment: set[str],
        known_focus_trees: set[str],
        entries: list[tuple[str, str, str | None, str | None]] | None = None,
        entry_scope: str = COUNTRY,
    ) -> list[ValidationError]:
        """Check what the mod's script names against what exists.

        The ``known_*`` sets are what the caller already has; a name missing
        from them is looked up in the complete catalog of the mod and the game
        before it is reported.
        """

        errors: list[ValidationError] = []
        owners = self._reference_owners(entries, entry_scope)
        catalogs = _LazyCatalogs(
            self,
            lambda: {entry["id"] for entry in self.state_index()},
            ideas=known_ideas,
            events=known_events,
            technologies=known_technologies,
            equipment=known_equipment,
            focus_trees=known_focus_trees,
        )

        def report_reference(
            message: str,
            code: str,
            owner: ScriptOwner,
            file_path: str | None,
            ref: ScriptRef,
            *,
            severity: Severity = "warning",
            idea_id: str | None = None,
            event_id: str | None = None,
        ) -> None:
            errors.append(
                self._script_ref_error(
                    message, code, owner.kind, owner.id, file_path,
                    idea_id=idea_id, event_id=event_id, severity=severity, line=ref.line,
                    column=ref.column,
                )
            )

        for owner, file_path in owners:
            kind, obj_id = owner.kind, owner.id
            has_tree_load = any(True for _ in owner.of("focus_tree"))
            for ref in owner.refs:
                if ref.kind == "recruit_character" and kind in _RUNTIME_OWNERS:
                    report_reference(
                        (
                            f"{kind} '{obj_id}' uses recruit_character at runtime. "
                            "HOI4 only executes recruit_character from game/history "
                            "at scenario start; recruit the character in country "
                            "history and gate its role availability instead."
                        ),
                        "runtime_recruit_character", owner, file_path, ref, severity="error",
                    )
                elif ref.dynamic and ref.kind in _NAMED_KINDS:
                    # The game builds the name while it runs.
                    continue
                elif ref.kind == "idea":
                    if not catalogs.has_idea(ref.value, show=ref.action == "show"):
                        report_reference(
                            f"{kind} '{obj_id}' references unknown idea '{ref.value}'"
                            + catalogs.case_hint("ideas", ref.value),
                            "unknown_idea_reference", owner, file_path, ref, idea_id=ref.value,
                        )
                    elif ref.key == "add_ideas":
                        idea = self._ideas.get(ref.value)
                        if idea is not None and idea.category in _HIRED_IDEA_CATEGORIES:
                            report_reference(
                                f"{kind} '{obj_id}' adds idea '{ref.value}', but its category "
                                f"is '{idea.category}', an advisor slot that is hired, not added",
                                "idea_not_addable", owner, file_path, ref, idea_id=ref.value,
                                severity="info",
                            )
                elif ref.kind == "event":
                    if not catalogs.has("events", ref.value):
                        report_reference(
                            f"{kind} '{obj_id}' references unknown event '{ref.value}'"
                            + catalogs.case_hint("events", ref.value),
                            "unknown_event_reference", owner, file_path, ref, event_id=ref.value,
                        )
                elif ref.kind == "focus_tree":
                    if not catalogs.has("focus_trees", ref.value):
                        report_reference(
                            f"{kind} '{obj_id}' references unknown focus tree '{ref.value}'",
                            "unknown_focus_tree_reference", owner, file_path, ref,
                        )
                elif ref.kind == "technology":
                    if not catalogs.has("technologies", ref.value):
                        report_reference(
                            f"{kind} '{obj_id}' references unknown technology '{ref.value}'",
                            "unknown_technology_reference", owner, file_path, ref,
                        )
                elif ref.kind == "equipment":
                    if not catalogs.has("equipment", ref.value):
                        report_reference(
                            f"{kind} '{obj_id}' references unknown equipment '{ref.value}'",
                            "unknown_equipment_reference", owner, file_path, ref,
                        )
                elif ref.kind == "civil_war_capital":
                    if ref.value.isdigit() and not catalogs.has_state(int(ref.value)):
                        report_reference(
                            f"{kind} '{obj_id}' uses civil-war capital {ref.value}, "
                            "which is not a known state ID",
                            "civil_war_capital_ref", owner, file_path, ref,
                        )
                elif ref.kind == "country":
                    if (
                        ref.statement
                        and kind in _SCRIPT_OWNERS
                        and not DYNAMIC_TAG_RE.match(ref.value)
                        and not catalogs.has_tag(ref.value)
                    ):
                        report_reference(
                            f"{kind} '{obj_id}' scopes into unknown country tag '{ref.value}'",
                            "unknown_country_scope", owner, file_path, ref,
                        )
                elif ref.kind == "core_effect":
                    if ref.certain and ref.scope == COUNTRY and kind in _SCRIPT_OWNERS:
                        report_reference(
                            _core_effect_message(ref),
                            "country_scope_core_effect", owner, file_path, ref,
                        )
                elif ref.kind == "faction_effect":
                    if ref.iterator == "country" and ref.certain:
                        report_reference(
                            f"{kind} '{obj_id}': 'add_to_faction' inside "
                            f"'{ref.scope_key}' runs once for every matched country, and "
                            "each of them adds the target to its own faction. Scope to the "
                            "faction leader instead, for example with "
                            "Mod.effect_add_target_to_faction(leader, target).",
                            "faction_scope_footgun", owner, file_path, ref, severity="info",
                        )
                elif ref.kind == "civil_war":
                    fields = set(ref.value.split("+"))
                    if not fields & {"capital", "states"}:
                        report_reference(
                            f"{kind} '{obj_id}': 'start_civil_war' names neither a capital "
                            "nor states, so the game picks the revolt's territory; "
                            "consider Mod.effect_spawn_revolution(...) for an explicit setup.",
                            "civil_war_scope_footgun", owner, file_path, ref, severity="info",
                        )
                    if not has_tree_load:
                        report_reference(
                            f"{kind} '{obj_id}': 'start_civil_war' is not paired with "
                            "load_focus_tree; the breakaway country keeps a copy of its "
                            "original's tree unless the script loads another one.",
                            "civil_war_focus_tree_missing", owner, file_path, ref,
                            severity="info",
                        )
            errors.extend(self._idea_tooltip_findings(owner, file_path))
        return errors

    def _idea_tooltip_findings(
        self, owner: ScriptOwner, file_path: str | None
    ) -> list[ValidationError]:
        """Advice: one effect that visibly removes several ideas and adds one."""

        removed: dict[int, list[ScriptRef]] = {}
        added: dict[int, int] = {}
        swapped: set[int] = set()
        for ref in owner.refs:
            if ref.kind == "swap_ideas":
                swapped.add(ref.root)
            if ref.kind != "idea" or ref.hidden or ref.guarded:
                continue
            if ref.key == "remove_ideas":
                removed.setdefault(ref.root, []).append(ref)
            elif ref.key == "add_ideas":
                added[ref.root] = added.get(ref.root, 0) + 1
        findings: list[ValidationError] = []
        for root, removals in removed.items():
            if len(removals) < 2 or not added.get(root) or root in swapped:
                continue
            findings.append(
                self._script_ref_error(
                    f"{owner.kind} '{owner.id}' removes {len(removals)} ideas and adds "
                    f"{added[root]} in one effect; consider Mod.effect_swap_idea() or "
                    "hidden effects for cleaner tooltips",
                    "bad_idea_tooltip_pattern", owner.kind, owner.id, file_path,
                    severity="info", line=removals[0].line, column=removals[0].column,
                )
            )
        return findings

    def _validate_state_effect_assumptions(
        self,
        *,
        entries: list[tuple[str, str, str | None, str | None]] | None = None,
    ) -> list[ValidationError]:
        """Advice about state effects that may not do what they seem to.

        Both findings are ``info``: the state's owner and cores at scenario
        start say little about the moment the effect runs.
        """

        errors: list[ValidationError] = []
        for owner, file_path in self._reference_owners(entries):
            kind, obj_id = owner.kind, owner.id
            for ref in owner.refs:
                if ref.guarded:
                    continue
                if ref.kind == "resistance" and ref.scope_key.isdigit():
                    state = self._read_state_readonly(int(ref.scope_key))
                    if state and state.owner and state.owner in state.cores:
                        errors.append(
                            self._script_ref_error(
                                (
                                    f"{kind} '{obj_id}' adds resistance to state {state.id}, "
                                    f"but its owner {state.owner} has a core there in history. "
                                    "Occupation resistance will not behave like generic unrest; "
                                    "use flags, variables, decisions, or revolt events."
                                ),
                                "resistance_on_core_state", kind, obj_id, file_path,
                                severity="info", line=ref.line, column=ref.column,
                            )
                        )
                elif (
                    ref.kind == "transfer_state"
                    and ref.value.isdigit()
                    and is_country_tag(ref.scope_key)
                ):
                    state = self._read_state_readonly(int(ref.value))
                    if state and state.owner == ref.scope_key:
                        errors.append(
                            self._script_ref_error(
                                (
                                    f"{kind} '{obj_id}' transfers state {state.id} to "
                                    f"{ref.scope_key}, which already owns it in history. "
                                    "Check released and existing-country edge cases."
                                ),
                                "revolt_state_already_owned", kind, obj_id, file_path,
                                severity="info", line=ref.line, column=ref.column,
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
        """Report shown text whose localisation key exists nowhere.

        Keys are looked up the way the game resolves them, in the mod, its
        base mods and the game (see ``_has_loc``):

        * events: title, description and option names, also the conditional
          ones; a hidden event shows no window and is skipped;
        * ideas: the ``name = KEY`` override or the idea id; hidden ideas
          and legacy advisor ideas (named through their traits) are skipped.
          A missing ``_desc`` is advice: the game then shows only the effects;
        * countries: ``TAG``, ``TAG_ADJ`` and ``TAG_DEF``, each also as an
          ideology variant (``TAG_fascism``, ``TAG_fascism_ADJ``...). Dynamic
          civil-war tags are named at runtime and skipped. ``TAG_DEF`` is
          advice: the game falls back to the plain name;
        * the country leader: a character whose ``name`` is plain text needs
          no key; otherwise its name key (or its id) must exist.
        """

        from ..characters import CountryLeaderRole
        from ..politics import RULING_PARTIES
        from ..syntax import parse
        from ..validation import event_is_hidden

        errors: list[ValidationError] = []

        def add_missing(
            message: str,
            *,
            optional: bool = False,
            focus_id: str | None = None,
            event_id: str | None = None,
            idea_id: str | None = None,
            country_tag: str | None = None,
            file_path: str | None = None,
        ) -> None:
            errors.append(
                ValidationError(
                    message=message,
                    severity="info" if optional else "warning",
                    code="missing_optional_localization" if optional else "missing_localization",
                    focus_id=focus_id,
                    event_id=event_id,
                    idea_id=idea_id,
                    country_tag=country_tag,
                    file_path=file_path,
                )
            )

        for event in self._events.values():
            if event_is_hidden(event):
                continue
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
            root = parse(idea.raw_block).root if idea.raw_block else None
            if idea.category == "hidden_ideas" or idea.traits or (
                root is not None and root.first("traits", fold=True) is not None
            ):
                continue
            name_key = (root.scalar("name", fold=True) if root is not None else None) or ""
            name_keys = [key for key in (name_key, idea.id) if key]
            file_path = str(idea.path) if idea.path else None
            if not any(self._has_loc(key) for key in name_keys):
                add_missing(
                    f"Idea '{idea.id}' localization not found "
                    f"(looked for {' or '.join(repr(key) for key in name_keys)})",
                    idea_id=idea.id,
                    file_path=file_path,
                )
            desc_keys = [f"{key}_desc" for key in name_keys]
            if not any(self._has_loc(key) for key in desc_keys):
                add_missing(
                    f"Idea '{idea.id}' description localization '{idea.id}_desc' not found",
                    optional=True,
                    idea_id=idea.id,
                    file_path=file_path,
                )

        ideologies = set(RULING_PARTIES) | set(self._vanilla_ideologies) | set(self._ideologies)
        # Tags declared after ``dynamic_tags = yes`` in a country_tags file
        # are civil-war tags the game names at runtime.
        dynamic_tags: set[str] = set()
        for _, text in self._effective_script_texts("common/country_tags"):
            try:
                dynamic_tags |= dynamic_tags_of(parse(text).root)
            except ValueError:
                continue
        for country in self._countries.values():
            if country.tag in dynamic_tags:
                continue
            for suffix, optional in (("", False), ("_ADJ", False), ("_DEF", True)):
                keys = [f"{country.tag}{suffix}"] + [
                    f"{country.tag}_{ideology}{suffix}" for ideology in sorted(ideologies)
                ]
                if not any(self._has_loc(key) for key in keys):
                    add_missing(
                        f"Country '{country.tag}' localization '{country.tag}{suffix}' not found "
                        "(nor an ideology variant)",
                        optional=optional,
                        country_tag=country.tag,
                    )
            leader = country.leader
            character = (
                self._characters.get(leader.character_id)
                if leader and leader.character_id
                else None
            )
            # Only a loaded character with a country-leader role is checked:
            # ``country.leader`` can name the first recruited advisor.
            if (
                leader is not None
                and character is not None
                and any(isinstance(role, CountryLeaderRole) for role in character.roles)
            ):
                name = character.name
                if name == leader.character_id:
                    name = ""
                # A name with spaces or other text is shown as written when
                # no key of that name exists; only key-like names are checked.
                key_like = bool(name) and "_" in name and re.fullmatch(r"[\w.\-]+", name)
                needed = name if key_like else ("" if name else leader.character_id)
                if needed and not self._has_loc(needed):
                    add_missing(
                        f"Country '{country.tag}' leader localization '{needed}' not found",
                        country_tag=country.tag,
                    )
        return errors

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
        for source in self._semantic_script_sources():
            kind = source.owner_kind
            if source.block is None or kind not in _MUTATING_OWNERS:
                continue
            ideas = _idea_mutation_ids(
                source.block,
                ignore_guarded_additions=kind != "focus",
            )
            if not ideas:
                continue
            target = focus_mutations if kind == "focus" else runtime_mutations
            target.setdefault(source.owner_id or "<unknown>", set()).update(ideas)
        focus_ideas = set().union(*focus_mutations.values()) if focus_mutations else set()
        runtime_ideas = set().union(*runtime_mutations.values()) if runtime_mutations else set()
        collisions = sorted(focus_ideas & runtime_ideas)
        if not collisions:
            return []
        # Point at the first focus that changes one of them.
        file_path: str | None = None
        line: int | None = None
        for owner, owner_path in self._reference_owners(None):
            if owner.kind != "focus":
                continue
            ref = next(
                (ref for ref in owner.of("idea") if ref.value in collisions and ref.action != "has"),
                None,
            )
            if ref is not None:
                file_path, line = owner_path, ref.line
                break
        return [
            ValidationError(
                message=(
                    "Focuses and runtime events/decisions mutate the same idea IDs "
                    f"({', '.join(collisions)}). Check delayed events cannot downgrade a staged spirit."
                ),
                severity="info",
                code="idea_mutation_collision",
                file_path=file_path,
                line=line,
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
        severity: Severity = "warning",
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
            decision_id=obj_id if kind == "decision" else None,
            idea_id=idea_id or (obj_id if kind == "idea" else None),
            line=line,
            column=column,
        )
