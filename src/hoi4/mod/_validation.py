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
from ..idea_icons import (
    resolve_idea_sprite,
)
from ..localisation import (
    LOCALISATION_LANGUAGES,
    localisation_header_language,
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
from ..syntax import parse, require_well_formed
from ..textio import read_text
from ..types import (
    Event,
    EventOption,
    Focus,
    FocusTree,
    ValidationError,
)
from ..validation import (
    DEFAULT_FOCUS_SEARCH_FILTERS,
    DEFAULT_IDEA_CATEGORIES,
    focus_search_filters_from_sprites,
    idea_categories_from_script,
    validate_country_history_references,
    validate_decision,
    validate_event,
    validate_focus_tree,
    validate_idea,
    validate_idea_category,
    validate_state,
    validate_tag_definition_targets,
    validation_suppressions,
)
from ..validation_stages import ValidationStage, require_validation_stage
from ._country_package import CountryPackageLayer
from ._definitions import DefinitionsLayer
from ._map import MapLayer
from ._script_validation import ScriptValidationLayer
from ._shared import _filter_validation_errors

_ISSUE_LOCATION_RE = re.compile(r"line (\d+), column (\d+)")

# ``_scan_cache`` entries of the localisation key indexes ``_has_loc`` builds;
# they are rebuilt for every ``validate()`` run.
_LOC_KEY_INDEXES = ("validation_loc_keys", "validation_mod_loc_keys")


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
        text = read_text(path)
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
                text = read_text(path)
                if path.is_relative_to(self.mod_root):
                    require_well_formed(text)
                return country_color_tags(text), None
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

    def _known_idea_categories(self) -> set[str]:
        """Idea categories and slots of the base game and every ``common/idea_tags`` file."""

        categories = set(DEFAULT_IDEA_CATEGORIES)
        for path in self.content_files("common/idea_tags").values():
            if path.suffix.lower() != ".txt":
                continue
            try:
                categories |= idea_categories_from_script(read_text(path))
            except (OSError, ValueError):
                continue
        return categories

    def _country_tag_aliases(self) -> set[str]:
        """Alias tags of ``common/country_tag_aliases``, usable wherever a tag is."""

        aliases: set[str] = set()
        for path in self.content_files("common/country_tag_aliases").values():
            if path.suffix.lower() != ".txt":
                continue
            try:
                document = parse(read_text(path))
            except (OSError, ValueError):
                continue
            aliases.update(item.name for item in document.root.assignments() if item.block)
        return aliases

    def _declared_event_namespaces(self) -> set[str]:
        """Every namespace an ``add_namespace`` declares, as the game sees them.

        A namespace declared in any event file of the game, a base mod or the
        mod can be used by every event, and a file may declare several. The
        files are read only when an event uses a namespace the loaded models
        do not already name.
        """

        names = {name for name in self._event_namespaces.values() if name}
        names.update(name for name in self._event_file_namespaces.values() if name)
        used = {event_id.split(".", 1)[0].lower() for event_id in self._events if "." in event_id}
        if used <= {name.lower() for name in names}:
            return names
        for path in self.content_files("events").values():
            if path.suffix.lower() != ".txt":
                continue
            try:
                document = parse(read_text(path))
            except (OSError, ValueError):
                continue
            for item in document.root.assignments("add_namespace", fold=True):
                if item.scalar:
                    names.add(item.scalar)
        return names

    def _known_focus_search_filters(self) -> set[str] | None:
        """Search filters that have a ``GFX_<filter>`` sprite, or ``None`` for the defaults.

        The sprites are scanned only when a focus uses a filter the base game
        list does not have.
        """

        used = {
            name
            for tree in self._focus_trees.values()
            for focus in tree.focuses
            for name in focus.search_filters
        }
        if used <= DEFAULT_FOCUS_SEARCH_FILTERS:
            return None
        return set(DEFAULT_FOCUS_SEARCH_FILTERS) | focus_search_filters_from_sprites(
            self._known_focus_icons()
        )

    def _focus_localization_findings(
        self, tree: FocusTree, focus: Focus
    ) -> list[ValidationError]:
        """Report a focus whose name or description key exists nowhere.

        The game names a focus by its ``text = KEY`` when it has one, else by
        its id, and describes it with that key plus ``_desc``.
        """

        if not focus.id:
            return []
        text_key = ""
        if focus.raw_block and "text" in focus.raw_block.lower():
            text_key = parse(focus.raw_block).root.scalar("text", fold=True) or ""
        name_keys = [key for key in (text_key, focus.id) if key]
        file_path = str(tree.path) if tree.path else None
        findings: list[ValidationError] = []
        if not any(self._has_loc(key) for key in name_keys):
            findings.append(
                ValidationError(
                    message=(
                        f"No localization found for focus '{focus.id}' "
                        f"(looked for {' or '.join(repr(key) for key in name_keys)})"
                    ),
                    severity="warning",
                    code="missing_localization",
                    focus_id=focus.id,
                    file_path=file_path,
                )
            )
        desc_keys = [f"{key}_desc" for key in name_keys]
        if not any(self._has_loc(key) for key in desc_keys):
            findings.append(
                ValidationError(
                    message=(
                        f"No description localization found for focus '{focus.id}' "
                        f"(looked for {' or '.join(repr(key) for key in desc_keys)})"
                    ),
                    severity="warning",
                    code="missing_localization",
                    focus_id=focus.id,
                    file_path=file_path,
                )
            )
        return findings

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
        *,
        advice: bool = False,
        suppress: Sequence[str] = (),
    ) -> list[ValidationError]:
        """Check the mod and return its findings.

        Each finding has a registered ``code`` and the severity of that code
        (see ``VALIDATION_SEVERITIES``): ``"error"`` means the game rejects
        the content or behaves wrongly, ``"warning"`` that it is probably a
        mistake, ``"info"`` is advice. Advice is returned only with
        ``advice=True``.

        ``suppress`` hides warnings and advice by code, either every finding
        of a code (``"visual_overlap"``) or only those about one object
        (``"missing_localization:TAG_my_focus"``). Errors cannot be
        suppressed, and an unknown code raises ``ValueError``.
        ``suppress_warnings`` is the older name of the same list.
        """

        # Checks share data built once per run (``_validation_run``); a
        # nested call reuses the outer run's data.
        outer = self._validation_run is None
        if outer:
            self._validation_run = {}
        try:
            return self._validate_once(
                suppress_warnings=suppress_warnings,
                validate_icons=validate_icons,
                strict_localization=strict_localization,
                stage=stage,
                script_token_allowlist=script_token_allowlist,
                liveness_flag_allowlist=liveness_flag_allowlist,
                liveness_localization_allowlist=liveness_localization_allowlist,
                progress=progress,
                cancelled=cancelled,
                advice=advice,
                suppress=suppress,
            )
        finally:
            if outer:
                self._validation_run = None

    def _validate_once(
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
        *,
        advice: bool = False,
        suppress: Sequence[str] = (),
    ) -> list[ValidationError]:
        """One validation run; see :meth:`validate`."""

        stage = require_validation_stage(stage)
        # Fail on a bad suppression before the long run, not after it.
        validation_suppressions([*(suppress_warnings or ()), *suppress])
        for name in _LOC_KEY_INDEXES:
            self._scan_cache.pop(name, None)
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
        known_tags.update(self._country_tag_aliases())
        # Country history may name an idea the mod does not define; the
        # reference checks load their catalogs themselves when a name is
        # not among the mod's own objects.
        need_ideas = any(
            idea_id not in self._ideas
            for country in self._countries.values()
            for idea_id in country.ideas
        )
        known_ideas = self._known_idea_ids() if need_ideas else set(self._ideas)
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
        errors.extend(self._validate_countries(known_parties))
        begin_phase("oobs", "Validating orders of battle")
        errors.extend(self._validate_country_oobs())
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
        declared_namespaces = self._declared_event_namespaces()
        for event in self._events.values():
            errors.extend(
                validate_event(event, namespaces=declared_namespaces)
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
                        validate_event(probe, namespace="on_action")
                    )

        for decision in self._decisions.values():
            errors.extend(validate_decision(decision))

        begin_phase("ideas", "Validating ideas and assignments")
        idea_categories = self._known_idea_categories() if self._ideas else set()
        for idea in self._ideas.values():
            errors.extend(validate_idea(idea))
            errors.extend(validate_idea_category(idea, idea_categories))
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

        # Country history may add ideas of any category (laws, advisors,
        # hidden ideas...), so only an unknown id is reported.
        for country in self._countries.values():
            for idea_id in country.ideas:
                if idea_id not in self._ideas and idea_id not in known_ideas:
                    errors.append(
                        ValidationError(
                            message=f"Country '{country.tag}' assigns unknown idea '{idea_id}'",
                            severity="warning",
                            code="unknown_assigned_idea",
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

        search_filters = self._known_focus_search_filters()
        for tree in self._focus_trees.values():
            tree_errors = validate_focus_tree(
                tree,
                known_focus_ids=all_focus_ids,
                known_state_ids=known_state_ids if known_state_ids else None,
                known_search_filters=search_filters,
            )
            errors.extend(tree_errors)

        for tree in self._focus_trees.values():
            for focus in tree.focuses:
                errors.extend(self._focus_localization_findings(tree, focus))
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
                known_events=set(self._events),
                known_technologies=set(),
                known_equipment=set(),
                known_focus_trees=set(self._focus_trees),
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
        findings = _filter_validation_errors(
            errors, suppress_warnings=suppress_warnings, suppress=suppress, advice=advice
        )
        for name in _LOC_KEY_INDEXES:
            self._scan_cache.pop(name, None)
        report_progress(
            progress,
            operation="validation",
            phase="done",
            current=phase_total,
            total=phase_total,
            message=f"Validation complete with {len(findings)} issue(s)",
        )
        return findings

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
