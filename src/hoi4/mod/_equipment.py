"""Equipment-variant and technology history analysis."""

from __future__ import annotations

import dataclasses
import re
from typing import Literal, Sequence, cast

from .._disk_cache import PAIRS_ON_DISK
from ..oob import (
    EquipmentVariant,
    find_equipment_variants,
)
from ..patching import (
    AssignmentSpan,
    top_level_assignments,
)
from ..syntax import parse
from ..types import (
    Country,
    ValidationError,
)
from ._catalogs import CatalogsLayer


def _special_project_unlocks(text: str) -> tuple[tuple[str, str], ...]:
    """``(equipment, "sp:<project>")`` for each equipment a project enables, in order."""

    pairs: list[tuple[str, str]] = []
    for project in parse(text).root.assignments():
        if project.block is None:
            continue
        for output in project.block.assignments("project_output"):
            if output.block is None:
                continue
            for enabled in output.block.assignments("enable_equipments"):
                if enabled.block is None:
                    continue
                for equipment in enabled.block.values():
                    pairs.append((equipment.value, f"sp:{project.name}"))
    return tuple(pairs)

_DATE_ASSIGNMENT_RE = re.compile(r"\d{1,4}\.\d{1,2}\.\d{1,2}(?:\.\d{1,2})?")


_ConditionAtom = tuple[Literal["dlc", "opaque"], str]


_ScriptCondition = tuple[tuple[_ConditionAtom, ...], tuple[_ConditionAtom, ...]]


_ScriptFormula = tuple[_ScriptCondition, ...]


@dataclasses.dataclass(frozen=True)
class _HistoryTechnologyGrant:
    technology: str
    condition: _ScriptCondition
    offset: int


@dataclasses.dataclass(frozen=True)
class _HistoryVariantOccurrence:
    variant: EquipmentVariant
    condition: _ScriptCondition
    offset: int


class EquipmentLayer(CatalogsLayer):
    """Equipment-variant and technology history analysis."""

    def _validate_equipment_variant_unlocks(
        self,
        country: Country,
    ) -> list[ValidationError]:
        unlock_map = self._equipment_unlock_technologies()
        for equipment, projects in self._special_project_unlocks().items():
            unlock_map[equipment] = (*unlock_map.get(equipment, ()), *projects)
        grants, occurrences = self._history_equipment_operations(country.raw_history)
        grants.extend(
            self._modeled_technology_grants_missing_from_history(country, grants)
        )
        errors: list[ValidationError] = []
        reported_offsets: set[int] = set()
        for occurrence in occurrences:
            variant = occurrence.variant
            if variant.equipment_type.startswith("ship_hull_"):
                dlc = "Man the Guns"
            elif "airframe" in variant.equipment_type:
                dlc = "By Blood Alone"
            else:
                continue
            # Hulls and airframes only exist with their DLC, so only the
            # paths where it is active can miss the chassis technology.
            required, excluded = occurrence.condition
            condition: _ScriptCondition = ((*required, ("dlc", dlc)), excluded)
            unlocks = unlock_map.get(variant.equipment_type, ())
            if not unlocks:
                continue
            available_conditions: list[_ScriptCondition] = [
                grant.condition
                for grant in grants
                if grant.technology in unlocks
                and (grant.offset < occurrence.offset or grant.offset < 0)
            ]
            if self._script_conditions_are_covered(
                condition,
                available_conditions,
            ):
                continue
            if occurrence.offset in reported_offsets:
                continue
            reported_offsets.add(occurrence.offset)
            line, column = self._line_column(country.raw_history, occurrence.offset)
            errors.append(
                ValidationError(
                    message=(
                        f"Country '{country.tag}' creates modular variant "
                        f"'{variant.name}' ({variant.equipment_type}) before its "
                        "chassis technology is unlocked on every matching DLC path. "
                        "Grant one of these technologies earlier in the same or a "
                        f"broader DLC branch: {', '.join(unlocks)}. "
                        "allow_without_tech is insufficient."
                    ),
                    severity="error",
                    code="equipment_variant_chassis_not_unlocked",
                    country_tag=country.tag,
                    file_path=(
                        str(country.history_path)
                        if country.history_path is not None
                        else None
                    ),
                    line=line,
                    column=column,
                )
            )
        return errors

    def _special_project_unlocks(self) -> dict[str, tuple[str, ...]]:
        """Equipment that special projects enable, as ``sp:<project>``.

        The country package check asks once per country; within one
        validate() run the files do not change, so they are read once.
        """

        return dict(self._run_cached("special_project_unlocks", self._read_special_project_unlocks))

    def _read_special_project_unlocks(self) -> dict[str, tuple[str, ...]]:
        unlocks: dict[str, list[str]] = {}
        for _, pairs in self._effective_script_results(
            "common/special_projects/projects",
            "special_project_unlocks",
            _special_project_unlocks,
            PAIRS_ON_DISK,
        ):
            for equipment, project in pairs:
                unlocks.setdefault(equipment, []).append(project)
        return {key: tuple(value) for key, value in unlocks.items()}

    @classmethod
    def _history_equipment_operations(
        cls,
        history: str,
    ) -> tuple[list[_HistoryTechnologyGrant], list[_HistoryVariantOccurrence]]:
        """Return ordered scenario-start technology and variant operations.

        Boolean ``AND``/``OR``/``NOT`` gates are retained so a technology must
        cover every satisfiable path where a variant can run. Each non-DLC
        predicate occurrence is a symbolic atom shared by its conditional body
        and remaining ``else_if``/``else`` chain. Later dated history is
        intentionally excluded.
        """

        grants: list[_HistoryTechnologyGrant] = []
        variants: list[_HistoryVariantOccurrence] = []

        true_formula: _ScriptFormula = (((), ()),)
        false_formula: _ScriptFormula = ()

        def normalize(conditions: Sequence[_ScriptCondition]) -> _ScriptFormula:
            result: list[_ScriptCondition] = []
            seen: set[_ScriptCondition] = set()
            for required, excluded in conditions:
                normalized = (
                    tuple(dict.fromkeys(required)),
                    tuple(dict.fromkeys(excluded)),
                )
                if set(normalized[0]) & set(normalized[1]) or normalized in seen:
                    continue
                seen.add(normalized)
                result.append(normalized)
            return tuple(result)

        def formula_or(
            left: _ScriptFormula,
            right: _ScriptFormula,
        ) -> _ScriptFormula:
            return normalize((*left, *right))

        def formula_and(
            left: _ScriptFormula,
            right: _ScriptFormula,
        ) -> _ScriptFormula:
            combined: list[_ScriptCondition] = []
            for left_required, left_excluded in left:
                for right_required, right_excluded in right:
                    combined.append(
                        (
                            (*left_required, *right_required),
                            (*left_excluded, *right_excluded),
                        )
                    )
            return normalize(combined)

        def formula_not(formula: _ScriptFormula) -> _ScriptFormula:
            result = true_formula
            for required, excluded in formula:
                negated_clause: _ScriptFormula = normalize(
                    [
                        *(((), (atom,)) for atom in required),
                        *(((atom,), ()) for atom in excluded),
                    ]
                )
                result = formula_and(result, negated_clause)
            return result

        def script_formula(
            fragment: str,
            fragment_offset: int,
            *,
            operator: Literal["AND", "OR"] = "AND",
        ) -> _ScriptFormula:
            terms: list[_ScriptFormula] = []
            for item in top_level_assignments(fragment):
                term: _ScriptFormula
                if not item.is_block:
                    if item.key.lower() == "has_dlc":
                        value = fragment[item.value_start : item.value_end]
                        term = (
                            ((("dlc", value.strip().strip('"')),), ()),
                        )
                    else:
                        term = (
                            ((("opaque", str(fragment_offset + item.start)),), ()),
                        )
                elif item.body_start is not None and item.body_end is not None:
                    child = fragment[item.body_start : item.body_end]
                    key = item.key.upper()
                    if key in {"AND", "OR"}:
                        term = script_formula(
                            child,
                            fragment_offset + item.body_start,
                            operator=cast(Literal["AND", "OR"], key),
                        )
                    elif key == "NOT":
                        term = formula_not(
                            script_formula(
                                child,
                                fragment_offset + item.body_start,
                            )
                        )
                    else:
                        term = (
                            ((("opaque", str(fragment_offset + item.start)),), ()),
                        )
                else:
                    term = (
                        ((("opaque", str(fragment_offset + item.start)),), ()),
                    )
                terms.append(term)
            result = true_formula if operator == "AND" else false_formula
            combine = formula_and if operator == "AND" else formula_or
            for term in terms:
                result = combine(result, term)
            return result

        def process_conditional(
            child: str,
            child_offset: int,
            incoming: _ScriptFormula,
        ) -> _ScriptFormula:
            children = top_level_assignments(child)
            limit = next(
                (
                    item
                    for item in children
                    if item.key.lower() == "limit"
                    and item.is_block
                    and item.body_start is not None
                    and item.body_end is not None
                ),
                None,
            )
            predicate: _ScriptFormula
            if limit is not None:
                assert limit.body_start is not None
                assert limit.body_end is not None
                predicate = script_formula(
                    child[limit.body_start : limit.body_end],
                    child_offset + limit.body_start,
                )
            else:
                predicate = (
                    ((("opaque", f"missing-limit:{child_offset}"),), ()),
                )
            positive = formula_and(incoming, predicate)
            remaining = formula_and(incoming, formula_not(predicate))
            for item in children:
                key = item.key.lower()
                if key == "limit":
                    continue
                if key == "else_if" and item.is_block:
                    if item.body_start is None or item.body_end is None:
                        continue
                    remaining = process_conditional(
                        child[item.body_start : item.body_end],
                        child_offset + item.body_start,
                        remaining,
                    )
                    continue
                if key == "else" and item.is_block:
                    if item.body_start is None or item.body_end is None:
                        continue
                    walk(
                        child[item.body_start : item.body_end],
                        child_offset + item.body_start,
                        remaining,
                    )
                    remaining = false_formula
                    continue
                process_span(child, item, child_offset, positive)
            return remaining

        def process_span(
            fragment: str,
            span: AssignmentSpan,
            base_offset: int,
            conditions: _ScriptFormula,
        ) -> None:
            if not conditions:
                return
            if (
                not span.is_block
                or span.body_start is None
                or span.body_end is None
            ):
                return
            if _DATE_ASSIGNMENT_RE.fullmatch(span.key):
                return
            child = fragment[span.body_start : span.body_end]
            child_offset = base_offset + span.body_start
            if span.key.lower() == "if":
                process_conditional(child, child_offset, conditions)
                return
            if span.key == "set_technology":
                for technology in top_level_assignments(child):
                    if technology.is_block:
                        continue
                    value = child[
                        technology.value_start : technology.value_end
                    ].strip().lower()
                    if value in {"0", "no"}:
                        continue
                    for condition in conditions:
                        grants.append(
                            _HistoryTechnologyGrant(
                                technology=technology.key,
                                condition=condition,
                                offset=child_offset + technology.start,
                            )
                        )
                return
            if span.key == "complete_special_project":
                # A finished special project unlocks the equipment it
                # enables, like a technology; recorded as ``sp:<project>``.
                for field in top_level_assignments(child):
                    if field.key != "project" or field.is_block:
                        continue
                    project = child[field.value_start : field.value_end]
                    project = project.strip().strip('"').removeprefix("sp:")
                    for condition in conditions:
                        grants.append(
                            _HistoryTechnologyGrant(
                                technology=f"sp:{project}",
                                condition=condition,
                                offset=child_offset + field.start,
                            )
                        )
                return
            if span.key == "create_equipment_variant":
                parsed = find_equipment_variants(fragment[span.start : span.end])
                if parsed:
                    for condition in conditions:
                        required_dlc = tuple(
                            value
                            for kind, value in condition[0]
                            if kind == "dlc"
                        )
                        excluded_dlc = tuple(
                            value
                            for kind, value in condition[1]
                            if kind == "dlc"
                        )
                        variants.append(
                            _HistoryVariantOccurrence(
                                variant=dataclasses.replace(
                                    parsed[0],
                                    required_dlc=required_dlc,
                                    excluded_dlc=excluded_dlc,
                                ),
                                condition=condition,
                                offset=base_offset + span.start,
                            )
                        )
                return
            walk(child, child_offset, conditions)

        def walk(
            fragment: str,
            base_offset: int,
            conditions: _ScriptFormula,
        ) -> None:
            for span in top_level_assignments(fragment):
                process_span(
                    fragment,
                    span,
                    base_offset,
                    conditions,
                )

        walk(history, 0, true_formula)
        grants.sort(key=lambda item: item.offset)
        variants.sort(key=lambda item: item.offset)
        return grants, variants

    @staticmethod
    def _modeled_technology_grants_missing_from_history(
        country: Country,
        history_grants: Sequence[_HistoryTechnologyGrant],
    ) -> list[_HistoryTechnologyGrant]:
        """Represent unsaved model-only technologies as unconditional grants."""

        history_ids = {grant.technology for grant in history_grants}
        return [
            _HistoryTechnologyGrant(technology, ((), ()), -1)
            for technology, level in country.technologies.items()
            if level and technology not in history_ids
        ]

    @staticmethod
    def _dlc_script_condition(
        required_dlc: Sequence[str],
        excluded_dlc: Sequence[str],
    ) -> _ScriptCondition:
        return (
            tuple(("dlc", value) for value in required_dlc),
            tuple(("dlc", value) for value in excluded_dlc),
        )

    @staticmethod
    def _script_conditions_are_covered(
        effect_condition: _ScriptCondition,
        grant_conditions: Sequence[_ScriptCondition],
    ) -> bool:
        """Return whether grants cover every feasible script-condition path."""

        assignment: dict[_ConditionAtom, bool] = {}
        for atom in effect_condition[0]:
            assignment[atom] = True
        for atom in effect_condition[1]:
            if assignment.get(atom) is True:
                return True  # The variant condition itself is impossible.
            assignment[atom] = False

        clauses: list[dict[_ConditionAtom, bool]] = []
        for required, excluded in grant_conditions:
            clause: dict[_ConditionAtom, bool] = {}
            impossible = False
            for atom in required:
                clause[atom] = True
            for atom in excluded:
                if clause.get(atom) is True:
                    impossible = True
                    break
                clause[atom] = False
            if not impossible:
                clauses.append(clause)

        def has_uncovered_path(
            index: int,
            current: dict[_ConditionAtom, bool],
        ) -> bool:
            if index == len(clauses):
                return True
            clause = clauses[index]
            if any(
                name in current and current[name] is not expected
                for name, expected in clause.items()
            ):
                return has_uncovered_path(index + 1, current)
            undecided = [name for name in clause if name not in current]
            if not undecided:
                return False
            for name in undecided:
                branch = dict(current)
                branch[name] = not clause[name]
                if has_uncovered_path(index + 1, branch):
                    return True
            return False

        return not has_uncovered_path(0, assignment)

    @classmethod
    def _history_technology_grants(cls, history: str) -> set[str]:
        """Return scenario-start technologies, excluding later dated blocks."""

        grants, _ = cls._history_equipment_operations(history)
        return {grant.technology for grant in grants}
