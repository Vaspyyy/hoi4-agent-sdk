"""Source-preserving definitions for keyed ``common/`` support domains.

Many HoI4 database folders share one of three layouts:

* top-level keyed definitions (``my_effect = { ... }``), such as scripted
  effects, static modifiers, game rules and AI strategies;
* keyed definitions inside a container block (``technologies = { tech = {
  ... } }``), such as technologies, sub-units, leader traits and wargoals;
* repeated entry blocks whose identifier is a field (``defined_text = { name =
  GetFoo ... }``), such as scripted localisation and autonomy states.

:class:`DefinitionKind` describes those layouts once so the :class:`~hoi4.Mod`
facade can list, create, update and delete definitions in every domain with the
same transactional, byte-preserving patches.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Iterable, Mapping, Sequence

from ._scalar import needs_quotes
from .parser import parse_pdx
from .patching import (
    AssignmentSpan,
    _dedent_block_body,
    _indent,
    _line_ending,
    _render_block_body_like,
    append_assignment,
    block_bodies_equivalent,
    replace_assignment,
    replace_assignment_body,
    top_level_assignments,
)

DEFINITION_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")


@dataclass(frozen=True)
class DefinitionKind:
    """How one ``common/`` domain stores its keyed definitions."""

    name: str
    directory: str
    container: str | None = None
    entry_key: str | None = None
    id_field: str = "name"
    recursive: bool = False
    default_file: str = "00_sdk_generated.txt"
    description: str = ""

    @property
    def default_path(self) -> str:
        return f"{self.directory}/{self.default_file}"


@dataclass(frozen=True)
class ScriptDefinition:
    """One effective definition, read from the winning file or a pending edit."""

    kind: str
    id: str
    body: str
    relative_path: str
    source_path: Path
    pending: bool = False


def _kind(name: str, directory: str, description: str, **kwargs: object) -> DefinitionKind:
    return DefinitionKind(name=name, directory=directory, description=description, **kwargs)  # type: ignore[arg-type]


DEFINITION_KINDS: dict[str, DefinitionKind] = {
    kind.name: kind
    for kind in (
        _kind("scripted_effect", "common/scripted_effects", "Reusable effect blocks."),
        _kind("scripted_trigger", "common/scripted_triggers", "Reusable trigger blocks."),
        _kind(
            "scripted_localisation", "common/scripted_localisation",
            "Dynamic [GetName] localisation selected by triggers.",
            entry_key="defined_text", id_field="name",
        ),
        _kind(
            "country_leader_trait", "common/country_leader",
            "Country-leader and advisor traits.", container="leader_traits",
        ),
        _kind(
            "unit_leader_trait", "common/unit_leader",
            "Army and navy commander traits.", container="leader_traits",
        ),
        _kind("technology", "common/technologies", "Research items.", container="technologies"),
        _kind("sub_unit", "common/units", "Battalion and company types.", container="sub_units"),
        _kind("equipment", "common/units/equipment", "Equipment archetypes and models.", container="equipment"),
        _kind("ai_strategy", "common/ai_strategy", "AI diplomatic and military strategies."),
        _kind("ai_strategy_plan", "common/ai_strategy_plans", "Long-term AI focus/strategy plans."),
        _kind("ai_template", "common/ai_templates", "AI division-template roles."),
        _kind("ai_equipment", "common/ai_equipment", "AI equipment-design roles."),
        _kind("ai_peace", "common/ai_peace", "AI peace-conference behaviour."),
        _kind("ai_area", "common/ai_areas", "Named AI geographic areas.", container="areas"),
        _kind(
            "opinion_modifier", "common/opinion_modifiers",
            "Diplomatic opinion and trade modifiers.", container="opinion_modifiers",
        ),
        _kind("static_modifier", "common/modifiers", "Named static modifiers."),
        _kind("wargoal", "common/wargoals", "War-goal types.", container="wargoal_types"),
        _kind(
            "autonomy_state", "common/autonomous_states",
            "Subject autonomy levels.", entry_key="autonomy_state", id_field="id",
        ),
        _kind("game_rule", "common/game_rules", "Game-setup rules and options."),
        _kind(
            "state_category", "common/state_category",
            "State categories and their building slots.", container="state_categories",
        ),
        _kind("building", "common/buildings", "Building types.", container="buildings"),
        _kind("resource", "common/resources", "Strategic resources.", container="resources"),
        _kind("ability", "common/abilities", "Unit-leader abilities.", container="ability"),
        _kind(
            "scripted_diplomatic_action", "common/scripted_diplomatic_actions",
            "Custom diplomacy-screen actions.", container="scripted_diplomatic_actions",
        ),
        _kind("scripted_gui", "common/scripted_guis", "Scripted GUI windows.", container="scripted_gui"),
        _kind("unit_name_list", "common/units/names", "Air/army unit name lists by country."),
        _kind("division_name_list", "common/units/names_divisions", "Division name groups."),
        _kind("ship_name_list", "common/units/names_ships", "Ship name groups."),
        _kind("balance_of_power", "common/bop", "Balance-of-power bars."),
        _kind(
            "military_industrial_organization",
            "common/military_industrial_organization/organizations",
            "Military-industrial organisations.",
        ),
        _kind("special_project", "common/special_projects/projects", "Special projects."),
        _kind("operation", "common/operations", "Intelligence operations."),
    )
}


def get_definition_kind(kind: str | DefinitionKind) -> DefinitionKind:
    if isinstance(kind, DefinitionKind):
        return kind
    try:
        return DEFINITION_KINDS[kind]
    except KeyError:
        known = ", ".join(sorted(DEFINITION_KINDS))
        raise ValueError(f"Unknown definition kind {kind!r}; expected one of: {known}") from None


def require_definition_id(definition_id: str) -> str:
    if not DEFINITION_ID_RE.fullmatch(definition_id or ""):
        raise ValueError(f"Invalid definition identifier: {definition_id!r}")
    return definition_id


def in_domain(kind: DefinitionKind, relative: Path) -> bool:
    directory = Path(kind.directory)
    if relative.suffix != ".txt":
        return False
    if kind.recursive:
        return relative.is_relative_to(directory)
    return relative.parent == directory


def _shift(span: AssignmentSpan, offset: int) -> AssignmentSpan:
    return replace(
        span,
        start=span.start + offset,
        end=span.end + offset,
        value_start=span.value_start + offset,
        value_end=span.value_end + offset,
        body_start=None if span.body_start is None else span.body_start + offset,
        body_end=None if span.body_end is None else span.body_end + offset,
    )


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _entry_id(text: str, span: AssignmentSpan, id_field: str) -> str | None:
    if span.body_start is None or span.body_end is None:
        return None
    body = text[span.body_start : span.body_end]
    for child in top_level_assignments(body):
        if child.key == id_field and not child.is_block:
            return _unquote(body[child.value_start : child.value_end])
    return None


def _container_spans(text: str, kind: DefinitionKind) -> list[AssignmentSpan]:
    return [
        span for span in top_level_assignments(text)
        if span.key == kind.container and span.is_block and span.body_start is not None
    ]


def definition_spans(text: str, kind: DefinitionKind) -> list[tuple[str, AssignmentSpan]]:
    """Return ``(id, span)`` pairs in absolute ``text`` coordinates."""

    result: list[tuple[str, AssignmentSpan]] = []
    if kind.container is not None:
        for container in _container_spans(text, kind):
            assert container.body_start is not None and container.body_end is not None
            inner = text[container.body_start : container.body_end]
            for span in top_level_assignments(inner):
                if span.is_block and not span.key.startswith("@"):
                    result.append((span.key, _shift(span, container.body_start)))
        return result
    for span in top_level_assignments(text):
        if not span.is_block:
            continue
        if kind.entry_key is not None:
            if span.key == kind.entry_key:
                entry_id = _entry_id(text, span, kind.id_field)
                if entry_id is not None:
                    result.append((entry_id, span))
        elif not span.key.startswith("@"):
            result.append((span.key, span))
    return result


def definition_body(text: str, kind: DefinitionKind, span: AssignmentSpan) -> str:
    """Return the editable body, omitting the identifier field of entry blocks."""

    assert span.body_start is not None and span.body_end is not None
    body = text[span.body_start : span.body_end]
    if kind.entry_key is not None:
        for child in top_level_assignments(body):
            if child.key == kind.id_field and not child.is_block:
                body = replace_assignment(body, child, None)
                break
    return _dedent_block_body(body)


def _id_value(definition_id: str) -> str:
    return f'"{definition_id}"' if needs_quotes(definition_id) else definition_id


def _full_body(kind: DefinitionKind, definition_id: str, body: str) -> str:
    cleaned = _dedent_block_body(body)
    if kind.entry_key is None:
        return cleaned
    head = f"{kind.id_field} = {_id_value(definition_id)}"
    return head + ("\n" + cleaned if cleaned else "")


def check_definition_body(kind: DefinitionKind, definition_id: str, body: str) -> str:
    """Return the cleaned body after proving it stays inside one block."""

    require_definition_id(definition_id)
    key = kind.entry_key or definition_id
    parsed = parse_pdx(f"{key} = {{\n{body}\n}}")
    nodes = [node for node in parsed.children if not node.is_comment]
    if len(nodes) != 1 or nodes[0].key != key or not nodes[0].is_block():
        raise ValueError("Definition body must not escape its enclosing block")
    if kind.entry_key is not None:
        for child in top_level_assignments(body):
            if child.key == kind.id_field:
                raise ValueError(
                    f"Pass the identifier as definition_id, not as {kind.id_field!r} in the body"
                )
    return _dedent_block_body(body)


def _render_assignment(key: str, body: str) -> str:
    rendered = f"{key} = {{"
    if body:
        rendered += "\n" + _indent(body, 1) + "\n"
    return rendered + "}"


def upsert_definition(text: str, kind: DefinitionKind, definition_id: str, body: str) -> str:
    """Create or replace one definition, editing only its own bytes."""

    full = _full_body(kind, definition_id, body)
    newline = _line_ending(text) if text else "\n"
    for existing_id, span in definition_spans(text, kind):
        if existing_id != definition_id:
            continue
        assert span.body_start is not None and span.body_end is not None
        current = text[span.body_start : span.body_end]
        if block_bodies_equivalent(current, full):
            return text
        line_start = text.rfind("\n", 0, span.start) + 1
        indent_match = re.match(r"[ \t]*", text[line_start : span.start])
        indent = indent_match.group(0) if indent_match else ""
        return replace_assignment_body(
            text, span, _render_block_body_like(current, full, newline, indent)
        )
    if kind.container is not None:
        containers = _container_spans(text, kind)
        if containers:
            container = containers[-1]
            assert container.body_start is not None and container.body_end is not None
            inner = text[container.body_start : container.body_end]
            if inner.strip():
                updated_inner = append_assignment(inner, _render_assignment(definition_id, full))
            else:
                updated_inner = (
                    newline + _indent(_render_assignment(definition_id, full), 1).replace("\n", newline)
                    + newline
                )
            return replace_assignment_body(text, container, updated_inner)
        rendered = _render_assignment(kind.container, _render_assignment(definition_id, full))
    else:
        rendered = _render_assignment(kind.entry_key or definition_id, full)
    if not text:
        return rendered + newline
    updated = append_assignment(text, rendered)
    if not updated.endswith(("\n", "\r")):
        updated += newline
    return updated


def remove_definition(text: str, kind: DefinitionKind, definition_id: str) -> str:
    for existing_id, span in definition_spans(text, kind):
        if existing_id == definition_id:
            return replace_assignment(text, span, None)
    return text


def read_definitions(
    text: str, kind: DefinitionKind, *, relative_path: str, source_path: Path, pending: bool,
) -> Iterable[ScriptDefinition]:
    for definition_id, span in definition_spans(text, kind):
        yield ScriptDefinition(
            kind=kind.name,
            id=definition_id,
            body=definition_body(text, kind, span),
            relative_path=relative_path,
            source_path=source_path,
            pending=pending,
        )


# ── Body renderers used by typed facade helpers ─────────────────────────


def _scalar(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value)
    return f'"{text}"' if needs_quotes(text) else text


def render_scalar_mapping(values: Mapping[str, object]) -> str:
    return "\n".join(f"{key} = {_scalar(value)}" for key, value in values.items())


def render_block(key: str, body: str) -> str:
    return _render_assignment(key, _dedent_block_body(body))


def render_scripted_localisation(entries: Sequence[tuple[str, str]]) -> str:
    """Render ``text`` alternatives; an empty trigger is an unconditional fallback."""

    if not entries:
        raise ValueError("Scripted localisation needs at least one entry")
    blocks: list[str] = []
    for trigger, key in entries:
        if not key or not DEFINITION_ID_RE.fullmatch(key):
            raise ValueError(f"Invalid localisation key: {key!r}")
        parts = [render_block("trigger", trigger)] if trigger.strip() else []
        parts.append(f"localization_key = {key}")
        blocks.append(render_block("text", "\n".join(parts)))
    return "\n".join(blocks)


def render_ai_strategy(
    strategies: Sequence[tuple[str, str, int]],
    *,
    allowed: str = "",
    enable: str = "",
    abort: str = "",
    extra: str = "",
) -> str:
    parts: list[str] = []
    for key, value in (("allowed", allowed), ("enable", enable), ("abort", abort)):
        if value.strip():
            parts.append(render_block(key, value))
    if not strategies:
        raise ValueError("An AI strategy needs at least one ai_strategy entry")
    for strategy_type, target, weight in strategies:
        line = f"type = {strategy_type}"
        if target:
            line += f" id = {_scalar(target)}"
        line += f" value = {int(weight)}"
        parts.append(f"ai_strategy = {{ {line} }}")
    if extra.strip():
        parts.append(_dedent_block_body(extra))
    return "\n".join(parts)
