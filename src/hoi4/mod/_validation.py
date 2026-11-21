"""The staged ``validate()`` entry point and related reports."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Sequence

from ..content_graph import ContentLivenessReport, analyze_content_liveness
from ..content_validation import (
    validate_bookmark,
    validate_dynamic_modifier,
    validate_ideology,
)
from ..countries import (
    country_color_tags,
)
from ..focus_layout import resolve_focus_positions, visual_overlap_issues
from ..idea_icons import (
    resolve_idea_sprite,
)
from ..localisation import (
    LOCALISATION_LANGUAGES,
    localisation_header_language,
)
from ..oob import (
    validate_oob,
)
from ..parser import (
    iter_assignment_blocks,
)
from ..patching import (
    top_level_assignments,
)
from ..progress import CancelCallback, ProgressCallback, check_cancelled, report_progress
from ..script import (
    validate_script_syntax,
)
from ..script_vocabulary import (
    validate_script_sources,
)
from ..tags import (
    load_all_tags,
)
from ..types import (
    Event,
    EventOption,
    Idea,
    ValidationError,
)
from ..validation import (
    validate_country,
    validate_country_history_references,
    validate_decision,
    validate_event,
    validate_focus_tree,
    validate_idea,
    validate_state,
    validate_tag_definition_targets,
)
from ..validation_stages import ValidationStage, require_validation_stage
from ._country_package import CountryPackageLayer
from ._definitions import DefinitionsLayer
from ._map import MapLayer
from ._script_validation import ScriptValidationLayer
from ._shared import (
    _EQUIPMENT_STOCKPILE_RE,
    _EVENT_REF_RE,
    _HAS_IDEA_RE,
    _IDEA_EFFECT_RE,
    _TECH_BLOCK_RE,
    _filter_validation_errors,
)

_ISSUE_LOCATION_RE = re.compile(r"line (\d+), column (\d+)")


_LOCALIZATION_HEADER_RE = re.compile(r"^l_[A-Za-z_]+\s*:")


def _validate_source_tree(mod_root: Path) -> list[ValidationError]:
    """Validate script and localization files even when no model loader owns them."""

    errors: list[ValidationError] = []
    script_paths: set[Path] = set()
    for directory_name in ("common", "events", "history", "interface"):
        directory = mod_root / directory_name
        if not directory.is_dir():
            continue
        for suffix in ("*.txt", "*.gfx"):
            script_paths.update(path for path in directory.rglob(suffix) if path.is_file())
    descriptor = mod_root / "descriptor.mod"
    if descriptor.is_file():
        script_paths.add(descriptor)

    for path in sorted(script_paths):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for issue in validate_script_syntax(text):
            location = _ISSUE_LOCATION_RE.search(issue)
            errors.append(
                ValidationError(
                    message=f"{path}: {issue}",
                    severity="error",
                    code="script_syntax",
                    file_path=str(path),
                    line=int(location.group(1)) if location else None,
                    column=int(location.group(2)) if location else None,
                )
            )
        try:
            has_unsupported_dynamic_ideas = any(
                span.key == "dynamic_country_ideas"
                for span in top_level_assignments(text)
            )
        except ValueError:
            has_unsupported_dynamic_ideas = False
        if has_unsupported_dynamic_ideas:
            errors.append(
                ValidationError(
                    message=(
                        f"{path}: dynamic_country_ideas is not a HOI4 construct. "
                        "Migrate this content to common/dynamic_modifiers and use "
                        "add_dynamic_modifier/remove_dynamic_modifier effects."
                    ),
                    severity="error",
                    code="unsupported_dynamic_country_ideas",
                    file_path=str(path),
                )
            )

        relative = path.relative_to(mod_root).as_posix()
        try:
            if relative.startswith("common/characters/"):
                for characters_body, characters_start, _ in iter_assignment_blocks(
                    text, "characters"
                ):
                    body_offset = text.find("{", characters_start) + 1
                    for character_span in top_level_assignments(characters_body):
                        if (
                            not character_span.is_block
                            or character_span.body_start is None
                            or character_span.body_end is None
                        ):
                            continue
                        character_body = characters_body[
                            character_span.body_start : character_span.body_end
                        ]
                        for role_span in top_level_assignments(character_body):
                            if role_span.key != "roles":
                                continue
                            absolute = body_offset + character_span.body_start + role_span.start
                            errors.append(
                                ValidationError(
                                    message=(
                                        f"{path}: character '{character_span.key}' uses "
                                        "unsupported top-level 'roles ='. Declare "
                                        "country_leader, advisor, corps_commander, or "
                                        "another role block directly."
                                    ),
                                    severity="error",
                                    code="invalid_character_roles_key",
                                    file_path=str(path),
                                    line=text.count("\n", 0, absolute) + 1,
                                )
                            )

            if relative.startswith("common/decisions/") and not relative.startswith(
                "common/decisions/categories/"
            ):
                for category_span in top_level_assignments(text):
                    if (
                        not category_span.is_block
                        or category_span.body_start is None
                        or category_span.body_end is None
                    ):
                        continue
                    category_body = text[
                        category_span.body_start : category_span.body_end
                    ]
                    metadata = next(
                        (
                            span
                            for span in top_level_assignments(category_body)
                            if span.key in {"icon", "allowed", "visible"}
                        ),
                        None,
                    )
                    if metadata is None:
                        continue
                    absolute = category_span.body_start + metadata.start
                    errors.append(
                        ValidationError(
                            message=(
                                f"{path}: decision category '{category_span.key}' "
                                f"declares '{metadata.key}' beside its decisions. Move "
                                "category metadata to common/decisions/categories."
                            ),
                            severity="error",
                            code="invalid_decision_category_layout",
                            file_path=str(path),
                            line=text.count("\n", 0, absolute) + 1,
                        )
                    )

            for politics_body, politics_start, _ in iter_assignment_blocks(
                text, "set_politics"
            ):
                invalid = next(
                    (
                        span
                        for span in top_level_assignments(politics_body)
                        if span.key == "elections_frequency"
                    ),
                    None,
                )
                if invalid is None:
                    continue
                body_offset = text.find("{", politics_start) + 1
                errors.append(
                    ValidationError(
                        message=(
                            f"{path}: set_politics does not support "
                            "elections_frequency in current HOI4."
                        ),
                        severity="error",
                        code="invalid_set_politics_field",
                        file_path=str(path),
                        line=text.count("\n", 0, body_offset + invalid.start) + 1,
                    )
                )
        except ValueError:
            # The syntax failure above already reports malformed source.
            pass

    localization = mod_root / "localisation"
    if localization.is_dir():
        for path in sorted(localization.rglob("*.yml")):
            raw = path.read_bytes()
            if not raw.startswith(b"\xef\xbb\xbf"):
                errors.append(
                    ValidationError(
                        message=f"Localization file is missing a UTF-8 BOM: {path}",
                        severity="error",
                        code="localization_bom",
                        file_path=str(path),
                        line=1,
                    )
                )
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                errors.append(
                    ValidationError(
                        message=f"Localization file is not valid UTF-8: {path}: {error}",
                        severity="error",
                        code="localization_encoding",
                        file_path=str(path),
                        line=1,
                    )
                )
                continue
            first_content = next(
                (
                    line.strip()
                    for line in text.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                ),
                "",
            )
            if not _LOCALIZATION_HEADER_RE.match(first_content):
                errors.append(
                    ValidationError(
                        message=f"Localization file has no l_<language>: header: {path}",
                        severity="error",
                        code="localization_header",
                        file_path=str(path),
                        line=1,
                    )
                )
                continue
            folder = path.relative_to(localization).parts[0].lower()
            header = localisation_header_language(text)
            if (
                folder in LOCALISATION_LANGUAGES
                and header is not None
                and header != folder
            ):
                errors.append(
                    ValidationError(
                        message=(
                            f"Localization file header 'l_{header}:' does not match its "
                            f"folder localisation/{folder}; HOI4 expects 'l_{folder}:': {path}"
                        ),
                        severity="error",
                        code="localization_language_mismatch",
                        file_path=str(path),
                        line=1,
                    )
                )
    return errors


class ValidationLayer(CountryPackageLayer, ScriptValidationLayer, DefinitionsLayer, MapLayer):
    """The staged ``validate()`` entry point and related reports."""

    def _validate_country_colors_shadow(self) -> list[ValidationError]:
        if self._base_content_root is None:
            return []
        mod_colors = self.mod_root / "common" / "countries" / "colors.txt"
        vanilla_colors = self._base_content_root / "common" / "countries" / "colors.txt"
        if not mod_colors.is_file() or not vanilla_colors.is_file():
            return []
        failed_color_paths = {
            diagnostic.path.resolve(strict=False)
            for diagnostic in self._load_diagnostics
            if diagnostic.section == "country_colors"
        }
        if (
            mod_colors.resolve(strict=False) in failed_color_paths
            or vanilla_colors.resolve(strict=False) in failed_color_paths
        ):
            return []

        def parse_tags(path: Path) -> tuple[set[str] | None, ValidationError | None]:
            try:
                return (
                    country_color_tags(
                        path.read_text(encoding="utf-8", errors="ignore")
                    ),
                    None,
                )
            except Exception as error:
                return (
                    None,
                    ValidationError(
                        message=(
                            f"Failed to validate country colors file {path}: "
                            f"{type(error).__name__}: {error}"
                        ),
                        severity="error",
                        code="country_colors_parse",
                        file_path=str(path),
                    ),
                )

        mod_tags, error = parse_tags(mod_colors)
        if error is not None:
            return [error]
        vanilla_tags, error = parse_tags(vanilla_colors)
        if error is not None:
            return [error]
        assert mod_tags is not None
        assert vanilla_tags is not None
        missing = sorted(vanilla_tags - mod_tags)
        if not missing:
            return []
        sample = ", ".join(missing[:8])
        if len(missing) > 8:
            sample += ", …"
        return [
            ValidationError(
                message=(
                    f"{mod_colors} defines {len(mod_tags)} country color entries but "
                    f"the configured HOI4 install defines {len(vanilla_tags)}. HOI4 "
                    f"shadows this file wholesale; {len(missing)} vanilla entries are "
                    f"missing ({sample}). Remove colors.txt for mod-only tags or seed it "
                    "from vanilla before overriding an existing tag."
                ),
                severity="warning",
                code="country_colors_shadow_vanilla",
                file_path=str(mod_colors),
            )
        ]

    # ── Validation ───────────────────────────────────────────────

    def validate(
        self,
        suppress_warnings: list[str] | tuple[str, ...] | set[str] | None = None,
        validate_icons: bool = False,
        strict_localization: bool = False,
        stage: ValidationStage = "package",
        script_token_allowlist: Sequence[str] = (),
        liveness_flag_allowlist: Sequence[str] = (),
        liveness_localization_allowlist: Sequence[str] = (),
        progress: ProgressCallback | None = None,
        cancelled: CancelCallback | None = None,
    ) -> list[ValidationError]:
        stage = require_validation_stage(stage)
        errors: list[ValidationError] = []
        phase_total = 12 + (1 if self.hoi4_install is not None else 0)
        if stage == "build":
            phase_total -= 1
        elif stage == "release":
            phase_total += 1
        phase_current = 0

        def begin_phase(phase: str, message: str) -> None:
            nonlocal phase_current
            check_cancelled(cancelled, operation="validation")
            report_progress(
                progress,
                operation="validation",
                phase=phase,
                current=phase_current,
                total=phase_total,
                message=message,
            )
            phase_current += 1

        begin_phase("load_diagnostics", "Checking load and duplicate diagnostics")

        duplicate_codes = {
            "country_tag": "duplicate_country_tag",
            "character": "duplicate_character_id",
            "state": "duplicate_state_id",
            "focus_tree": "duplicate_focus_tree_id",
            "focus": "duplicate_focus_id",
            "event": "duplicate_event_id",
            "decision_category": "duplicate_decision_category_id",
            "decision": "duplicate_decision_id",
            "idea": "duplicate_idea_id",
            "ideology": "duplicate_ideology_id",
            "dynamic_modifier": "duplicate_dynamic_modifier_id",
            "bookmark": "duplicate_bookmark_name",
        }
        for diagnostic in self._load_diagnostics:
            if diagnostic.error_type != "DuplicateIdentifierError":
                errors.append(
                    ValidationError(
                        message=(
                            f"Failed to load {diagnostic.section} file "
                            f"{diagnostic.path}: {diagnostic.error_type}: "
                            f"{diagnostic.message}"
                        ),
                        severity="error",
                        code="load_failure",
                        file_path=str(diagnostic.path),
                    )
                )
                continue
            errors.append(
                ValidationError(
                    message=diagnostic.message,
                    severity="error",
                    code=duplicate_codes.get(
                        diagnostic.section, "duplicate_identifier"
                    ),
                    file_path=str(diagnostic.path),
                    related_file_path=(
                        str(diagnostic.related_path)
                        if diagnostic.related_path is not None
                        else None
                    ),
                    country_tag=(
                        diagnostic.identifier
                        if diagnostic.section == "country_tag"
                        else None
                    ),
                    state_id=(
                        int(diagnostic.identifier)
                        if diagnostic.section == "state" and diagnostic.identifier
                        else None
                    ),
                    event_id=(
                        diagnostic.identifier
                        if diagnostic.section == "event"
                        else None
                    ),
                    idea_id=(
                        diagnostic.identifier
                        if diagnostic.section == "idea"
                        else None
                    ),
                    decision_id=(
                        diagnostic.identifier
                        if diagnostic.section == "decision"
                        else None
                    ),
                    focus_tree_id=(
                        diagnostic.identifier
                        if diagnostic.section == "focus_tree"
                        else None
                    ),
                    ideology_id=(
                        diagnostic.identifier
                        if diagnostic.section == "ideology"
                        else None
                    ),
                    dynamic_modifier_id=(
                        diagnostic.identifier
                        if diagnostic.section == "dynamic_modifier"
                        else None
                    ),
                    bookmark_name=(
                        diagnostic.identifier
                        if diagnostic.section == "bookmark"
                        else None
                    ),
                )
            )

        begin_phase("source_files", "Checking all script and localization source files")
        errors.extend(_validate_source_tree(self.mod_root))

        begin_phase("catalogs", "Resolving referenced game and mod catalogs")
        known_tags = set(load_all_tags(self._base_content_root, self.mod_root))
        known_tags.update(self._countries.keys())
        script_entries = self._script_entries()
        scripts = "\n".join(entry[0] for entry in script_entries)
        need_ideas = any(country.ideas for country in self._countries.values()) or bool(
            _IDEA_EFFECT_RE.search(scripts) or _HAS_IDEA_RE.search(scripts)
        )
        known_ideas = self._known_idea_ids() if need_ideas else set(self._ideas)
        known_events = (
            self._known_event_ids() if _EVENT_REF_RE.search(scripts) else set(self._events)
        )
        known_technologies = (
            self._known_technology_ids() if _TECH_BLOCK_RE.search(scripts) else set()
        )
        known_equipment = (
            self._known_equipment_ids() if _EQUIPMENT_STOCKPILE_RE.search(scripts) else set()
        )
        known_focus_trees = set(self._focus_trees)
        known_focus_icons = (
            self._known_focus_icons()
            if validate_icons and (self._focus_trees or self._ideas)
            else set()
        )
        known_state_ids = {entry["id"] for entry in self.state_index()}
        known_character_ids = self._known_character_ids()
        errors.extend(validate_tag_definition_targets(self.mod_root))
        errors.extend(
            validate_country_history_references(
                self.mod_root,
                known_state_ids=known_state_ids,
                known_character_ids=known_character_ids,
            )
        )

        begin_phase("countries", "Validating countries")
        errors.extend(self._validate_country_colors_shadow())
        known_parties = set(self._vanilla_ideologies) | set(self._ideologies)
        for country in list(self._countries.values()):
            errors.extend(validate_country(country, known_parties=known_parties))
            errors.extend(self._validate_equipment_variant_unlocks(country))
        begin_phase("oobs", "Validating land orders of battle")
        validated_oobs: set[str] = set()
        for country in list(self._countries.values()):
            for reference in self._country_oob_references(country):
                try:
                    oob = self.get_oob(reference.name)
                except KeyError:
                    errors.append(
                        ValidationError(
                            message=(
                                f"Country '{country.tag}' references missing "
                                f"{reference.kind} OOB '{reference.name}'"
                            ),
                            severity="error",
                            code="missing_oob_reference",
                            country_tag=country.tag,
                        )
                    )
                    continue
                oob.country_tag = country.tag
                oob.kind = reference.kind
                oob.required_dlc = reference.required_dlc
                oob.excluded_dlc = reference.excluded_dlc
                if oob.name not in validated_oobs:
                    errors.extend(validate_oob(oob))
                    validated_oobs.add(oob.name)
                errors.extend(self._validate_oob_context(oob, country.tag))
        for name, oob in self._oobs.items():
            if name not in validated_oobs:
                errors.extend(validate_oob(oob))
                if oob.country_tag:
                    errors.extend(
                        self._validate_oob_context(oob, oob.country_tag)
                    )
        begin_phase("states", "Validating states")
        for state_id in sorted(set(self._state_ids) | set(self._states)):
            check_cancelled(cancelled, operation="validation")
            try:
                state = self.get_state(state_id)
            except (KeyError, OSError, ValueError) as error:
                errors.append(
                    ValidationError(
                        message=f"Could not validate state {state_id}: {error}",
                        severity="error",
                        code="state_load_error",
                        state_id=state_id,
                        file_path=(
                            str(self._state_source_paths[state_id])
                            if state_id in self._state_source_paths
                            else None
                        ),
                    )
                )
                continue
            errors.extend(validate_state(state, known_tags))
        errors.extend(self._validate_map_editing())

        if stage != "build":
            begin_phase("country_packages", "Validating complete SDK-created countries")
            existing_findings = {
                (
                    finding.code,
                    finding.message,
                    finding.file_path,
                    finding.country_tag,
                    finding.state_id,
                )
                for finding in errors
            }
            for tag in sorted(self._created_country_tags & set(self._countries)):
                package = self.validate_country_package(tag)
                for finding in package.findings:
                    identity = (
                        finding.code,
                        finding.message,
                        finding.file_path,
                        finding.country_tag,
                        finding.state_id,
                    )
                    if identity in existing_findings:
                        continue
                    errors.append(finding)
                    existing_findings.add(identity)

        begin_phase("events", "Validating events and on-actions")
        for event_id, event in self._events.items():
            errors.extend(
                validate_event(
                    event, namespace=self._event_namespaces.get(event_id), known_tags=known_tags
                )
            )

        for occurrences in self._on_action_occurrences.values():
            for action in occurrences:
                if action.effect:
                    probe = Event(
                        id=f"on_action.{action.id}",
                        title=action.id,
                        description=action.id,
                        options=[
                            EventOption(name=f"on_action.{action.id}.a", effect=action.effect),
                        ],
                    )
                    errors.extend(
                        validate_event(probe, namespace="on_action", known_tags=known_tags)
                    )

        for decision in self._decisions.values():
            errors.extend(validate_decision(decision))

        begin_phase("ideas", "Validating ideas and assignments")
        for idea in self._ideas.values():
            errors.extend(validate_idea(idea))
            if (
                validate_icons
                and known_focus_icons
                and resolve_idea_sprite(idea.icon) not in known_focus_icons
            ):
                suggestions = self.suggest_idea_icons(idea.icon, count=1)
                suggestion = (
                    f" Suggested close match: {suggestions[0]}"
                    if suggestions
                    else ""
                )
                errors.append(
                    ValidationError(
                        message=(
                            f"Idea '{idea.id}' picture stem '{idea.icon}' did not "
                            f"resolve to sprite '{resolve_idea_sprite(idea.icon)}'."
                            f"{suggestion}"
                        ),
                        severity="warning",
                        code="unknown_idea_icon",
                        idea_id=idea.id,
                        file_path=str(idea.path) if idea.path else None,
                    )
                )

        for ideology in self._ideologies.values():
            errors.extend(validate_ideology(ideology))
        for dynamic_modifier in self._dynamic_modifiers.values():
            errors.extend(validate_dynamic_modifier(dynamic_modifier))
        for bookmark in self._bookmarks:
            errors.extend(validate_bookmark(bookmark))

        law_category_cache: dict[tuple[str, str], bool] = {}

        def is_declared_law(idea: Idea) -> bool:
            key = (idea.category, idea.category_raw_block)
            if key not in law_category_cache:
                law_category_cache[key] = any(
                    span.key == "law" and not span.is_block
                    and idea.category_raw_block[span.value_start:span.value_end].strip().strip('"') == "yes"
                    for span in top_level_assignments(idea.category_raw_block)
                )
            return law_category_cache[key]

        for country in self._countries.values():
            for idea_id in country.ideas:
                assigned_idea = self._ideas.get(idea_id)
                if assigned_idea is None and idea_id not in known_ideas:
                    errors.append(
                        ValidationError(
                            message=f"Country '{country.tag}' assigns unknown idea '{idea_id}'",
                            severity="warning",
                            code="unknown_assigned_idea",
                            country_tag=country.tag,
                            idea_id=idea_id,
                        )
                    )
                elif (assigned_idea is not None and assigned_idea.category != "country"
                      and not is_declared_law(assigned_idea)):
                    errors.append(
                        ValidationError(
                            message=(
                                f"Country '{country.tag}' assigns idea '{idea_id}', but that idea is in category "
                                f"'{assigned_idea.category or '<none>'}', not 'country' or a declared law category."
                            ),
                            severity="warning",
                            code="assigned_idea_not_country_category",
                            country_tag=country.tag,
                            idea_id=idea_id,
                        )
                    )

        begin_phase("focus_catalog", "Preparing focus validation context")
        all_focus_ids: set[str] = set()
        begin_phase("focus_trees", "Validating focus trees")
        for tree in self._focus_trees.values():
            check_cancelled(cancelled, operation="validation")
            all_focus_ids.update(f.id for f in tree.focuses)

        icon_suggestions: dict[str, str] = {}
        if validate_icons and known_focus_icons:
            for icon in {
                focus.icon
                for tree in self._focus_trees.values()
                for focus in tree.focuses
                if focus.icon not in known_focus_icons
            }:
                icon_suggestions[icon] = self.suggest_focus_icon(icon)

        for tree in self._focus_trees.values():
            tree_errors = validate_focus_tree(
                tree,
                known_focus_ids=all_focus_ids,
                known_state_ids=known_state_ids if known_state_ids else None,
                known_tags=known_tags,
            )
            errors.extend(tree_errors)
            overlap_issues = visual_overlap_issues(tree, resolve_focus_positions(tree))
            if overlap_issues:
                errors.append(
                    ValidationError(
                        message=f"Focus tree '{tree.id}' visual overlap risk: {'; '.join(overlap_issues)}",
                        severity="warning",
                        code="visual_overlap",
                        file_path=str(tree.path) if tree.path else None,
                    )
                )

        for tree in self._focus_trees.values():
            for focus in tree.focuses:
                loc_key = f"{focus.id}:0"
                if loc_key not in self._loc_entries and focus.id not in self._loc_entries:
                    errors.append(
                        ValidationError(
                            message=f"No localization found for focus '{focus.id}'",
                            severity="warning",
                            code="missing_localization",
                            focus_id=focus.id,
                            file_path=str(tree.path) if tree.path else None,
                        )
                    )
                desc_key = f"{focus.id}_desc"
                desc_colon_key = f"{focus.id}_desc:0"
                if desc_key not in self._loc_entries and desc_colon_key not in self._loc_entries:
                    errors.append(
                        ValidationError(
                            message=f"No description localization found for focus '{focus.id}'",
                            severity="warning",
                            code="missing_localization",
                            focus_id=focus.id,
                            file_path=str(tree.path) if tree.path else None,
                        )
                    )
                if validate_icons and known_focus_icons and focus.icon not in known_focus_icons:
                    suggestion = icon_suggestions[focus.icon]
                    errors.append(
                        ValidationError(
                            message=f"Focus '{focus.id}' icon '{focus.icon}' was not found. Suggested close match: {suggestion}",
                            severity="warning",
                            code="unknown_focus_icon",
                            focus_id=focus.id,
                            file_path=str(tree.path) if tree.path else None,
                        )
                    )

        begin_phase("references", "Validating cross-content references")
        errors.extend(
            self._validate_script_references(
                known_ideas=known_ideas,
                known_events=known_events,
                known_technologies=known_technologies,
                known_equipment=known_equipment,
                known_focus_trees=known_focus_trees,
            )
        )
        errors.extend(self._validate_state_effect_assumptions())
        errors.extend(self._validate_idea_mutation_collisions())
        errors.extend(self._validate_definitions())
        if self.hoi4_install is not None:
            begin_phase(
                "script_vocabulary",
                "Checking effect, trigger, and modifier names",
            )
            errors.extend(
                validate_script_sources(
                    self._semantic_script_sources(),
                    self.game_script_vocabulary(),
                    mod_root=self.mod_root,
                    custom_tokens=self._effective_custom_script_tokens(),
                    allowlist=script_token_allowlist,
                )
            )
        errors.extend(self._validate_localization_languages())
        if strict_localization:
            errors.extend(self._validate_localization_references())
        if stage == "release":
            begin_phase("content_liveness", "Analyzing semantic content liveness")
            errors.extend(
                self.analyze_content_liveness(
                    flag_allowlist=liveness_flag_allowlist,
                    localization_allowlist=liveness_localization_allowlist,
                ).findings
            )

        check_cancelled(cancelled, operation="validation")
        report_progress(
            progress,
            operation="validation",
            phase="done",
            current=phase_total,
            total=phase_total,
            message=f"Validation complete with {len(errors)} issue(s)",
        )
        return _filter_validation_errors(errors, suppress_warnings=suppress_warnings)

    def analyze_content_liveness(
        self,
        *,
        flag_allowlist: Sequence[str] = (),
        localization_allowlist: Sequence[str] = (),
    ) -> ContentLivenessReport:
        """Return focus/event/idea/flag/localization liveness diagnostics."""

        return analyze_content_liveness(
            self,
            flag_allowlist=flag_allowlist,
            localization_allowlist=localization_allowlist,
        )

    def validate_game_log(
        self,
        log_path: str | Path,
        *,
        since: datetime | None = None,
        start_offset: int = 0,
        incremental: bool = False,
        require_fresh: bool = False,
    ) -> list[ValidationError]:
        """Return HOI4 engine errors attributable to files in this mod.

        ``incremental=True`` retains the trailing record until the next header.
        Use ``parse_hoi4_error_log`` to obtain a report with a resume offset;
        this facade returns only validation errors. The default flushes EOF.
        """

        from ..game_log import parse_hoi4_error_log

        fresh_after: datetime | None = None
        if require_fresh:
            mtimes = [
                path.stat().st_mtime
                for path in self.mod_root.rglob("*")
                if path.is_file()
            ]
            if mtimes:
                fresh_after = datetime.fromtimestamp(max(mtimes)).astimezone()
        report = parse_hoi4_error_log(
            log_path,
            self.mod_root,
            since=since,
            start_offset=start_offset,
            incremental=incremental,
            fresh_after=fresh_after,
        )
        return list(report.validation_errors)
