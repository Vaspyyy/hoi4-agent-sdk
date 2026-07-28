"""Source-preserving HOI4 character models and serializers.

Character definitions are unusually polymorphic: one character may expose
several roles, and DLC variants repeat ``instance`` blocks with independent
``allowed`` conditions.  This module retains those occurrences explicitly so
callers can edit them without flattening or discarding unmodeled source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeAlias, TypedDict, cast

from .patching import (
    AssignmentSpan,
    append_assignment,
    replace_assignment,
    replace_assignment_body,
    set_block,
    set_scalar,
    top_level_assignments,
)
from .script import pdx_string


PortraitChannel = Literal["civilian", "army", "navy"]
ArmyCommanderKind = Literal["corps_commander", "field_marshal"]
ROLE_KEYS = frozenset(
    {"country_leader", "advisor", "corps_commander", "field_marshal", "navy_leader"}
)


@dataclass
class CharacterPortrait:
    channel: PortraitChannel = "civilian"
    large: str = ""
    small: str = ""
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)


@dataclass
class CountryLeaderRole:
    ideology: str = "liberalism"
    expire: str = "1965.1.1.1"
    traits: list[str] = field(default_factory=list)
    id: int = -1
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_occurrence: int = -1


@dataclass
class AdvisorRole:
    slot: str = "political_advisor"
    traits: list[str] = field(default_factory=list)
    cost: int = 100
    idea_token: str = ""
    ledger: str = ""
    allowed: str = ""
    visible: str = ""
    available: str = ""
    ai_will_do: str = "factor = 1"
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_occurrence: int = -1


@dataclass
class ArmyCommanderRole:
    kind: ArmyCommanderKind = "corps_commander"
    skill: int = 1
    attack_skill: int = 1
    defense_skill: int = 1
    planning_skill: int = 1
    logistics_skill: int = 1
    traits: list[str] = field(default_factory=list)
    legacy_id: int = -1
    visible: str = ""
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_occurrence: int = -1

    def __post_init__(self) -> None:
        if self.kind not in {"corps_commander", "field_marshal"}:
            raise ValueError(
                "ArmyCommanderRole.kind must be 'corps_commander' or 'field_marshal'"
            )


@dataclass
class NavyLeaderRole:
    skill: int = 1
    attack_skill: int = 1
    defense_skill: int = 1
    maneuvering_skill: int = 1
    coordination_skill: int = 1
    traits: list[str] = field(default_factory=list)
    legacy_id: int = -1
    visible: str = ""
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_occurrence: int = -1


CharacterRole: TypeAlias = (
    CountryLeaderRole | AdvisorRole | ArmyCommanderRole | NavyLeaderRole
)


class _ParsedScope(TypedDict):
    name: str
    portraits: list[CharacterPortrait]
    roles: list[CharacterRole]


@dataclass
class CharacterInstance:
    allowed: str = ""
    name: str = ""
    portraits: list[CharacterPortrait] = field(default_factory=list)
    roles: list[CharacterRole] = field(default_factory=list)
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_occurrence: int = -1


@dataclass
class Character:
    id: str
    country_tag: str = ""
    name: str = ""
    portraits: list[CharacterPortrait] = field(default_factory=list)
    roles: list[CharacterRole] = field(default_factory=list)
    instances: list[CharacterInstance] = field(default_factory=list)
    path: Path | None = None
    raw_block: str = ""
    touched_fields: set[str] = field(default_factory=set, repr=False)
    source_index: int = -1
    # In-memory authoring intent only. ``None`` means a character loaded from
    # source whose recruitment may legitimately happen through an event.
    recruitment_expected: bool | None = field(default=None, repr=False)


def role_key(role: CharacterRole) -> str:
    if isinstance(role, CountryLeaderRole):
        return "country_leader"
    if isinstance(role, AdvisorRole):
        return "advisor"
    if isinstance(role, ArmyCommanderRole):
        return role.kind
    return "navy_leader"


def load_characters_file(path: Path, *, country_tag: str = "") -> list[Character]:
    """Load every direct character definition from one source file."""

    text = path.read_text(encoding="utf-8", errors="ignore")
    wrapper = next(
        (
            span
            for span in top_level_assignments(text)
            if span.key == "characters"
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ),
        None,
    )
    body = (
        text[wrapper.body_start : wrapper.body_end]
        if wrapper is not None
        and wrapper.body_start is not None
        and wrapper.body_end is not None
        else text
    )
    result: list[Character] = []
    for source_index, span in enumerate(
        item
        for item in top_level_assignments(body)
        if item.is_block and item.body_start is not None and item.body_end is not None
    ):
        assert span.body_start is not None
        assert span.body_end is not None
        character_body = body[span.body_start : span.body_end]
        inferred_tag = country_tag or _infer_country_tag(span.key, path)
        result.append(
            _parse_character(
                span.key,
                character_body,
                path=path,
                country_tag=inferred_tag,
                source_index=source_index,
            )
        )
    return result


def serialize_character(character: Character, indent: int = 1) -> str:
    body = _patch_character_body(character.raw_block, character)
    if not character.raw_block:
        body = _patch_character_body("", character, all_fields=True)
    return _wrap_block(character.id, body, indent)


def serialize_characters_file(
    characters: list[Character],
    original: str = "",
) -> str:
    """Serialize a character file while retaining untouched source bytes."""

    if not original:
        lines = ["characters = {"]
        for character in characters:
            lines.append(serialize_character(character))
            lines.append("")
        lines.append("}")
        lines.append("")
        return "\n".join(lines)

    wrapper = next(
        (
            span
            for span in top_level_assignments(original)
            if span.key == "characters"
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ),
        None,
    )
    if wrapper is None or wrapper.body_start is None or wrapper.body_end is None:
        return _patch_characters_body(characters, original)
    body = original[wrapper.body_start : wrapper.body_end]
    return replace_assignment_body(
        original,
        wrapper,
        _patch_characters_body(characters, body),
    )


def _parse_character(
    character_id: str,
    body: str,
    *,
    path: Path,
    country_tag: str,
    source_index: int,
) -> Character:
    direct = _parse_scope(body)
    instances: list[CharacterInstance] = []
    occurrence = 0
    for span in top_level_assignments(body):
        if (
            span.key != "instance"
            or not span.is_block
            or span.body_start is None
            or span.body_end is None
        ):
            continue
        instance_body = body[span.body_start : span.body_end]
        parsed = _parse_scope(instance_body)
        instances.append(
            CharacterInstance(
                allowed=_block_value(instance_body, "allowed"),
                name=parsed["name"],
                portraits=parsed["portraits"],
                roles=parsed["roles"],
                raw_block=instance_body,
                source_occurrence=occurrence,
            )
        )
        occurrence += 1
    return Character(
        id=character_id,
        country_tag=country_tag,
        name=direct["name"],
        portraits=direct["portraits"],
        roles=direct["roles"],
        instances=instances,
        path=path,
        raw_block=body,
        source_index=source_index,
    )


def _parse_scope(body: str) -> _ParsedScope:
    roles: list[CharacterRole] = []
    occurrences: dict[str, int] = {}
    for span in top_level_assignments(body):
        if (
            span.key not in ROLE_KEYS
            or not span.is_block
            or span.body_start is None
            or span.body_end is None
        ):
            continue
        role_body = body[span.body_start : span.body_end]
        occurrence = occurrences.get(span.key, 0)
        occurrences[span.key] = occurrence + 1
        roles.append(_parse_role(span.key, role_body, occurrence))
    return {
        "name": _scalar_value(body, "name"),
        "portraits": _parse_portraits(body),
        "roles": roles,
    }


def _parse_role(key: str, body: str, occurrence: int) -> CharacterRole:
    if key == "country_leader":
        return CountryLeaderRole(
            ideology=_scalar_value(body, "ideology") or "liberalism",
            expire=_scalar_value(body, "expire") or "1965.1.1.1",
            traits=_block_tokens(body, "traits"),
            id=_int_value(body, "id", -1),
            raw_block=body,
            source_occurrence=occurrence,
        )
    if key == "advisor":
        return AdvisorRole(
            slot=_scalar_value(body, "slot") or "political_advisor",
            traits=_block_tokens(body, "traits"),
            cost=_int_value(body, "cost", 100),
            idea_token=_scalar_value(body, "idea_token"),
            ledger=_scalar_value(body, "ledger"),
            allowed=_block_value(body, "allowed"),
            visible=_block_value(body, "visible"),
            available=_block_value(body, "available"),
            ai_will_do=_block_value(body, "ai_will_do") or "factor = 1",
            raw_block=body,
            source_occurrence=occurrence,
        )
    if key in {"corps_commander", "field_marshal"}:
        return ArmyCommanderRole(
            kind=cast(ArmyCommanderKind, key),
            skill=_int_value(body, "skill", 1),
            attack_skill=_int_value(body, "attack_skill", 1),
            defense_skill=_int_value(body, "defense_skill", 1),
            planning_skill=_int_value(body, "planning_skill", 1),
            logistics_skill=_int_value(body, "logistics_skill", 1),
            traits=_block_tokens(body, "traits"),
            legacy_id=_int_value(body, "legacy_id", -1),
            visible=_block_value(body, "visible"),
            raw_block=body,
            source_occurrence=occurrence,
        )
    return NavyLeaderRole(
        skill=_int_value(body, "skill", 1),
        attack_skill=_int_value(body, "attack_skill", 1),
        defense_skill=_int_value(body, "defense_skill", 1),
        maneuvering_skill=_int_value(body, "maneuvering_skill", 1),
        coordination_skill=_int_value(body, "coordination_skill", 1),
        traits=_block_tokens(body, "traits"),
        legacy_id=_int_value(body, "legacy_id", -1),
        visible=_block_value(body, "visible"),
        raw_block=body,
        source_occurrence=occurrence,
    )


def _parse_portraits(body: str) -> list[CharacterPortrait]:
    portraits_body = _block_value(body, "portraits")
    if not portraits_body:
        return []
    result: list[CharacterPortrait] = []
    for span in top_level_assignments(portraits_body):
        if (
            span.key not in {"civilian", "army", "navy"}
            or not span.is_block
            or span.body_start is None
            or span.body_end is None
        ):
            continue
        channel_body = portraits_body[span.body_start : span.body_end]
        result.append(
            CharacterPortrait(
                channel=span.key,  # type: ignore[arg-type]
                large=_scalar_value(channel_body, "large"),
                small=_scalar_value(channel_body, "small"),
                raw_block=channel_body,
            )
        )
    return result


def _patch_character_body(
    body: str,
    character: Character,
    *,
    all_fields: bool = False,
) -> str:
    fields = {"name", "portraits", "roles", "instances"} if all_fields else character.touched_fields
    if "name" in fields:
        body = set_scalar(body, "name", pdx_string(character.name) if character.name else None)
    if "portraits" in fields:
        body = _patch_portraits(body, character.portraits)
    if "roles" in fields:
        body = _patch_roles(body, character.roles)
    if "instances" in fields:
        body = _patch_instances(body, character.instances)
    unknown = fields - {"name", "portraits", "roles", "instances"}
    if unknown:
        raise ValueError(f"Unknown character fields: {sorted(unknown)}")
    return body


def _patch_instance_body(
    body: str,
    instance: CharacterInstance,
    *,
    all_fields: bool = False,
) -> str:
    fields = {"allowed", "name", "portraits", "roles"} if all_fields else instance.touched_fields
    if "allowed" in fields:
        body = set_block(body, "allowed", instance.allowed or None)
    if "name" in fields:
        body = set_scalar(body, "name", pdx_string(instance.name) if instance.name else None)
    if "portraits" in fields:
        body = _patch_portraits(body, instance.portraits)
    if "roles" in fields:
        body = _patch_roles(body, instance.roles)
    unknown = fields - {"allowed", "name", "portraits", "roles"}
    if unknown:
        raise ValueError(f"Unknown character instance fields: {sorted(unknown)}")
    return body


def _patch_instances(body: str, instances: list[CharacterInstance]) -> str:
    source = {
        instance.source_occurrence: instance
        for instance in instances
        if instance.source_occurrence >= 0
    }
    matches: list[tuple[AssignmentSpan, CharacterInstance | None]] = []
    occurrence = 0
    for span in top_level_assignments(body):
        if span.key != "instance" or not span.is_block:
            continue
        matches.append((span, source.get(occurrence)))
        occurrence += 1
    for raw_span, instance in reversed(matches):
        span = raw_span
        if instance is None:
            body = replace_assignment(body, span, None)
            continue
        if span.body_start is None or span.body_end is None:
            body = replace_assignment(
                body,
                span,
                _wrap_block("instance", _patch_instance_body("", instance, all_fields=True), 0),
            )
            continue
        current = body[span.body_start : span.body_end]
        body = replace_assignment_body(
            body,
            span,
            _patch_instance_body(current, instance),
        )
    for instance in instances:
        if instance.source_occurrence >= 0:
            continue
        body = append_assignment(
            body,
            _wrap_block("instance", _patch_instance_body("", instance, all_fields=True), 0),
        )
    return body


def _patch_roles(body: str, roles: list[CharacterRole]) -> str:
    source = {
        (role_key(role), role.source_occurrence): role
        for role in roles
        if role.source_occurrence >= 0
    }
    occurrences: dict[str, int] = {}
    matches: list[tuple[AssignmentSpan, CharacterRole | None]] = []
    for span in top_level_assignments(body):
        if span.key not in ROLE_KEYS or not span.is_block:
            continue
        occurrence = occurrences.get(span.key, 0)
        occurrences[span.key] = occurrence + 1
        matches.append((span, source.get((span.key, occurrence))))
    for raw_span, role in reversed(matches):
        span = raw_span
        if role is None:
            body = replace_assignment(body, span, None)
            continue
        if span.body_start is None or span.body_end is None:
            body = replace_assignment(
                body,
                span,
                serialize_character_role(role),
            )
            continue
        current = body[span.body_start : span.body_end]
        body = replace_assignment_body(
            body,
            span,
            _patch_role_body(current, role),
        )
    for role in roles:
        if role.source_occurrence >= 0:
            continue
        body = append_assignment(body, serialize_character_role(role))
    return body


def serialize_character_role(role: CharacterRole) -> str:
    return _wrap_block(role_key(role), _patch_role_body("", role, all_fields=True), 0)


def _patch_role_body(
    body: str,
    role: CharacterRole,
    *,
    all_fields: bool = False,
) -> str:
    if isinstance(role, CountryLeaderRole):
        fields = {"ideology", "expire", "traits", "id"} if all_fields else role.touched_fields
        if "ideology" in fields:
            body = set_scalar(body, "ideology", role.ideology or None)
        if "expire" in fields:
            body = set_scalar(body, "expire", pdx_string(role.expire) if role.expire else None)
        if "traits" in fields:
            body = set_block(body, "traits", " ".join(role.traits) or None)
        if "id" in fields:
            body = set_scalar(body, "id", str(role.id))
        _raise_unknown(fields, {"ideology", "expire", "traits", "id"}, "country leader")
        return body
    if isinstance(role, AdvisorRole):
        fields = (
            {
                "slot",
                "traits",
                "cost",
                "idea_token",
                "ledger",
                "allowed",
                "visible",
                "available",
                "ai_will_do",
            }
            if all_fields
            else role.touched_fields
        )
        for key in ("slot", "idea_token", "ledger"):
            if key in fields:
                value = str(getattr(role, key))
                body = set_scalar(body, key, value or None)
        if "traits" in fields:
            body = set_block(body, "traits", " ".join(role.traits) or None)
        if "cost" in fields:
            body = set_scalar(body, "cost", str(role.cost))
        for key in ("allowed", "visible", "available", "ai_will_do"):
            if key in fields:
                body = set_block(body, key, str(getattr(role, key)) or None)
        _raise_unknown(
            fields,
            {
                "slot",
                "traits",
                "cost",
                "idea_token",
                "ledger",
                "allowed",
                "visible",
                "available",
                "ai_will_do",
            },
            "advisor",
        )
        return body
    if isinstance(role, ArmyCommanderRole):
        fields = (
            {
                "skill",
                "attack_skill",
                "defense_skill",
                "planning_skill",
                "logistics_skill",
                "traits",
                "legacy_id",
                "visible",
            }
            if all_fields
            else role.touched_fields
        )
        for key in (
            "skill",
            "attack_skill",
            "defense_skill",
            "planning_skill",
            "logistics_skill",
            "legacy_id",
        ):
            if key in fields:
                body = set_scalar(body, key, str(getattr(role, key)))
        if "traits" in fields:
            body = set_block(body, "traits", " ".join(role.traits) or None)
        if "visible" in fields:
            body = set_block(body, "visible", role.visible or None)
        _raise_unknown(
            fields,
            {
                "skill",
                "attack_skill",
                "defense_skill",
                "planning_skill",
                "logistics_skill",
                "traits",
                "legacy_id",
                "visible",
            },
            "army commander",
        )
        return body
    fields = (
        {
            "skill",
            "attack_skill",
            "defense_skill",
            "maneuvering_skill",
            "coordination_skill",
            "traits",
            "legacy_id",
            "visible",
        }
        if all_fields
        else role.touched_fields
    )
    for key in (
        "skill",
        "attack_skill",
        "defense_skill",
        "maneuvering_skill",
        "coordination_skill",
        "legacy_id",
    ):
        if key in fields:
            body = set_scalar(body, key, str(getattr(role, key)))
    if "traits" in fields:
        body = set_block(body, "traits", " ".join(role.traits) or None)
    if "visible" in fields:
        body = set_block(body, "visible", role.visible or None)
    _raise_unknown(
        fields,
        {
            "skill",
            "attack_skill",
            "defense_skill",
            "maneuvering_skill",
            "coordination_skill",
            "traits",
            "legacy_id",
            "visible",
        },
        "navy leader",
    )
    return body


def _patch_portraits(body: str, portraits: list[CharacterPortrait]) -> str:
    existing = _block_value(body, "portraits")
    if not portraits:
        return set_block(body, "portraits", None)
    patched = existing
    by_channel: dict[str, CharacterPortrait] = {
        portrait.channel: portrait for portrait in portraits
    }
    existing_channels = [
        span
        for span in top_level_assignments(existing)
        if span.key in {"civilian", "army", "navy"}
        and span.is_block
        and span.body_start is not None
        and span.body_end is not None
    ]
    for span in reversed(existing_channels):
        portrait = by_channel.pop(span.key, None)
        if portrait is None:
            patched = replace_assignment(patched, span, None)
            continue
        assert span.body_start is not None
        assert span.body_end is not None
        channel_body = patched[span.body_start : span.body_end]
        channel_body = set_scalar(channel_body, "large", portrait.large or None)
        channel_body = set_scalar(channel_body, "small", portrait.small or None)
        patched = replace_assignment_body(patched, span, channel_body)
    for portrait in by_channel.values():
        channel_body = ""
        channel_body = set_scalar(channel_body, "large", portrait.large or None)
        channel_body = set_scalar(channel_body, "small", portrait.small or None)
        patched = append_assignment(
            patched,
            _wrap_block(portrait.channel, channel_body, 0),
        )
    return set_block(body, "portraits", patched or None)


def _patch_characters_body(characters: list[Character], body: str) -> str:
    by_source = {
        character.source_index: character
        for character in characters
        if character.source_index >= 0
    }
    spans = [
        span
        for span in top_level_assignments(body)
        if span.is_block and span.body_start is not None and span.body_end is not None
    ]
    for source_index, span in reversed(list(enumerate(spans))):
        character = by_source.get(source_index)
        if character is None:
            body = replace_assignment(body, span, None)
            continue
        assert span.body_start is not None
        assert span.body_end is not None
        current = body[span.body_start : span.body_end]
        patched = _patch_character_body(current, character)
        if span.key != character.id:
            body = replace_assignment(body, span, serialize_character(character, indent=0))
        else:
            body = replace_assignment_body(body, span, patched)
    for character in characters:
        if character.source_index >= 0:
            continue
        body = append_assignment(body, serialize_character(character, indent=0))
    return body


def _scalar_value(body: str, key: str) -> str:
    for span in top_level_assignments(body):
        if span.key == key and not span.is_block:
            return _unquote(body[span.value_start : span.value_end])
    return ""


def _int_value(body: str, key: str, default: int) -> int:
    try:
        return int(_scalar_value(body, key))
    except ValueError:
        return default


def _block_value(body: str, key: str) -> str:
    for span in top_level_assignments(body):
        if (
            span.key == key
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ):
            return body[span.body_start : span.body_end]
    return ""


def _block_tokens(body: str, key: str) -> list[str]:
    value = _block_value(body, key)
    return [
        token
        for token in value.replace("\r", " ").replace("\n", " ").split()
        if not token.startswith("#")
    ]


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return text


def _wrap_block(key: str, body: str, indent: int) -> str:
    prefix = "\t" * indent
    inner = _indent(body, indent + 1)
    if inner:
        return f"{prefix}{key} = {{\n{inner}\n{prefix}}}"
    return f"{prefix}{key} = {{\n{prefix}}}"


def _indent(text: str, level: int) -> str:
    prefix = "\t" * level
    return "\n".join(prefix + line.rstrip() for line in text.strip().splitlines())


def _infer_country_tag(character_id: str, path: Path) -> str:
    prefix = character_id.split("_", 1)[0]
    if len(prefix) == 3 and prefix.isalnum() and prefix.upper() == prefix:
        return prefix
    stem_prefix = path.stem.split("_", 1)[0]
    return (
        stem_prefix
        if len(stem_prefix) == 3
        and stem_prefix.isalnum()
        and stem_prefix.upper() == stem_prefix
        else ""
    )


def _raise_unknown(fields: set[str], known: set[str], label: str) -> None:
    unknown = fields - known
    if unknown:
        raise ValueError(f"Unknown {label} fields: {sorted(unknown)}")
