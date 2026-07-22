"""
Event system - read, create, and write HOI4 events.

Events live in events/*.txt files. Each file declares a namespace and contains
one or more supported event blocks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .parser import find_assignment_block, strip_comments
from .patching import (
    AssignmentSpan,
    append_assignment,
    assignment_spans,
    block_bodies_equivalent,
    dedent_block_body,
    replace_assignment,
    replace_assignment_body,
    set_block,
    set_scalar,
    top_level_assignments,
)
from .script import normalize_block_body
from .types import Event, EventOption

NAMESPACE_RE = re.compile(r"add_namespace\s*=\s*(\S+)")
VALID_EVENT_TYPES = frozenset(
    {
        "country_event",
        "state_event",
        "news_event",
        "unit_leader_event",
        "operative_leader_event",
    }
)
EVENT_ID_RE = re.compile(r"\bid\s*=\s*(\S+)")
TITLE_RE = re.compile(r"\btitle\s*=\s*(\S+)")
DESC_RE = re.compile(r"\bdesc\s*=\s*(\S+)")
PICTURE_RE = re.compile(r"\bpicture\s*=\s*(\S+)")
TRIGGERED_ONLY_RE = re.compile(r"\bis_triggered_only\s*=\s*(yes|no)")


def scan_event_ids_file(path: Path) -> set[str]:
    """Extract event IDs without constructing full event/option models."""

    text = path.read_text(encoding="utf-8", errors="ignore")
    ids: set[str] = set()
    for span in top_level_assignments(text):
        if (
            not span.is_block
            or span.body_start is None
            or span.body_end is None
            or not (span.key in VALID_EVENT_TYPES or span.key.endswith("_event"))
        ):
            continue
        body = text[span.body_start : span.body_end]
        id_spans = assignment_spans(body, "id")
        if not id_spans or id_spans[0].is_block:
            continue
        raw = body[id_spans[0].value_start : id_spans[0].value_end].strip()
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            raw = raw[1:-1]
        if raw:
            ids.add(raw)
    return ids


def load_events_file(path: Path) -> tuple[Optional[str], list[Event]]:
    txt = path.read_text(encoding="utf-8", errors="ignore")

    ns_m = NAMESPACE_RE.search(strip_comments(txt))
    namespace = ns_m.group(1) if ns_m else None

    events: list[Event] = []
    event_blocks: list[tuple[int, str, str]] = []
    for span in top_level_assignments(txt):
        if (
            span.key in VALID_EVENT_TYPES
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ):
            event_blocks.append((span.start, span.key, txt[span.body_start : span.body_end]))
    for _, event_type, chunk in sorted(event_blocks, key=lambda item: item[0]):
        event = _parse_event_block(chunk, event_type, path)
        if event:
            events.append(event)

    return namespace, events


def _parse_event_block(chunk: str, event_type: str, path: Path) -> Optional[Event]:
    clean_chunk = strip_comments(chunk)
    id_m = EVENT_ID_RE.search(clean_chunk)
    if not id_m:
        return None

    event = Event(id=id_m.group(1), event_type=event_type, path=path, raw_block=chunk.strip())

    title_m = TITLE_RE.search(clean_chunk)
    if title_m:
        event.title = title_m.group(1)

    desc_m = DESC_RE.search(clean_chunk)
    if desc_m:
        event.description = desc_m.group(1)

    pic_m = PICTURE_RE.search(clean_chunk)
    if pic_m:
        event.picture = pic_m.group(1)

    triggered_m = TRIGGERED_ONLY_RE.search(clean_chunk)
    if triggered_m:
        event.is_triggered_only = triggered_m.group(1) == "yes"

    fire_m = re.search(r"\bfire_only_once\s*=\s*(yes|no)", clean_chunk)
    if fire_m:
        event.fire_only_once = fire_m.group(1) == "yes"

    trigger_body = _extract_block(chunk, "trigger")
    if trigger_body:
        event.trigger = trigger_body.strip()

    immediate_body = _extract_block(chunk, "immediate")
    if immediate_body:
        event.immediate = immediate_body.strip()

    mtt_body = _extract_block(chunk, "mean_time_to_happen")
    if mtt_body:
        event.mean_time_to_happen = mtt_body.strip()

    event.options = _extract_options(chunk)

    return event


def _extract_block(chunk: str, block_name: str) -> str:
    match = find_assignment_block(chunk, block_name)
    if not match:
        return ""
    return dedent_block_body(match[0])


def _extract_options(chunk: str) -> list[EventOption]:
    options: list[EventOption] = []
    for span in top_level_assignments(chunk):
        if (
            span.key != "option"
            or not span.is_block
            or span.body_start is None
            or span.body_end is None
        ):
            continue
        block_text = chunk[span.body_start : span.body_end]
        opt = EventOption(raw_block=block_text.strip())

        clean_block = strip_comments(block_text)
        name_m = re.search(r"\bname\s*=\s*(\S+)", clean_block)
        if name_m:
            opt.name = name_m.group(1)

        ai_body = _extract_block(block_text, "ai_chance")
        if ai_body:
            opt.ai_chance = ai_body.strip()

        trigger_body = _extract_block(block_text, "trigger")
        if trigger_body:
            opt.trigger = trigger_body.strip()

        cleaned = block_text.strip()
        if trigger_body:
            trigger_match = find_assignment_block(cleaned, "trigger")
            if trigger_match:
                before = cleaned[: trigger_match[1]].strip()
                after = cleaned[trigger_match[2] :].strip()
                cleaned = (before + "\n" + after).strip()
        ai_match = find_assignment_block(cleaned, "ai_chance")
        if ai_match:
            before = cleaned[: ai_match[1]].strip()
            after = cleaned[ai_match[2] :].strip()
            cleaned = (before + "\n" + after).strip()

        lines = []
        for line in cleaned.split("\n"):
            stripped = line.strip()
            if re.match(r"^name\s*=", stripped):
                continue
            lines.append(stripped)
        effect_text = "\n".join(lines).strip()
        opt.effect = normalize_block_body(effect_text)

        options.append(opt)

    return options


def serialize_event(event: Event) -> str:
    if event.raw_block:
        body = _patch_event_body(event, event.raw_block)
        return f"{event.event_type} = {{\n{_indent(body, 1)}\n}}"

    parts: list[str] = []
    parts.append(f"{event.event_type} = {{")
    parts.append(f"\tid = {event.id}")

    if event.title:
        parts.append(f"\ttitle = {event.title}")
    if event.description:
        parts.append(f"\tdesc = {event.description}")
    parts.append(f"\tpicture = {event.picture}")

    if event.is_triggered_only:
        parts.append("\tis_triggered_only = yes")
    if event.fire_only_once is not None:
        parts.append(f"\tfire_only_once = {'yes' if event.fire_only_once else 'no'}")

    trigger = normalize_block_body(event.trigger)
    immediate = normalize_block_body(event.immediate)
    mean_time_to_happen = normalize_block_body(event.mean_time_to_happen)

    if trigger:
        parts.append("")
        parts.append("\ttrigger = {")
        for line in trigger.split("\n"):
            parts.append(f"\t\t{line.strip()}")
        parts.append("\t}")

    if immediate:
        parts.append("")
        parts.append("\timmediate = {")
        for line in immediate.split("\n"):
            parts.append(f"\t\t{line.strip()}")
        parts.append("\t}")

    if mean_time_to_happen:
        parts.append("")
        parts.append("\tmean_time_to_happen = {")
        for line in mean_time_to_happen.split("\n"):
            parts.append(f"\t\t{line.strip()}")
        parts.append("\t}")

    for opt in event.options:
        parts.append("")
        parts.extend(_serialize_option(opt, indent=1).splitlines())

    parts.append("}")
    return "\n".join(parts)


def serialize_events_file(namespace: Optional[str], events: list[Event], original: str = "") -> str:
    if original:
        text = set_scalar(original, "add_namespace", namespace)
        current = {event.id: event for event in events}
        spans: list[tuple[str, AssignmentSpan]] = []
        for span in top_level_assignments(text):
            if (
                span.key not in VALID_EVENT_TYPES
                or not span.is_block
                or span.body_start is None
                or span.body_end is None
            ):
                continue
            body = text[span.body_start : span.body_end]
            match = EVENT_ID_RE.search(strip_comments(body))
            if match:
                spans.append((match.group(1), span))
        for event_id, span in sorted(spans, key=lambda item: item[1].start, reverse=True):
            event = current.pop(event_id, None)
            if event is None:
                text = replace_assignment(text, span, None)
            elif (
                event.raw_block
                and event.event_type == span.key
                and span.body_start is not None
                and span.body_end is not None
            ):
                event_body = text[span.body_start : span.body_end]
                text = replace_assignment_body(text, span, _patch_event_body(event, event_body))
            else:
                text = replace_assignment(text, span, serialize_event(event))
        for event in current.values():
            text = append_assignment(text, serialize_event(event))
        return text
    parts: list[str] = []
    if namespace:
        parts.append(f"add_namespace = {namespace}")
        parts.append("")

    for event in events:
        parts.append(serialize_event(event))
        parts.append("")

    return "\n".join(parts)


def write_events_file(path: Path, namespace: Optional[str], events: list[Event]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_events_file(namespace, events)
    path.write_text(content, encoding="utf-8")
    return path


def _serialize_option(option: EventOption, indent: int) -> str:
    prefix = "\t" * indent
    if option.raw_block:
        body = _patch_option_body(option, option.raw_block)
        return f"{prefix}option = {{\n{_indent(body, indent + 1)}\n{prefix}}}"
    lines = [f"{prefix}option = {{"]
    if option.name:
        lines.append(f"{prefix}\tname = {option.name}")
    for key, value in [("ai_chance", option.ai_chance), ("trigger", option.trigger)]:
        if value:
            lines.append(f"{prefix}\t{key} = {{")
            lines.extend(
                f"{prefix}\t\t{line.strip()}" for line in normalize_block_body(value).splitlines()
            )
            lines.append(f"{prefix}\t}}")
    if option.effect:
        lines.extend(f"{prefix}\t{line.strip()}" for line in option.effect.strip().splitlines())
    lines.append(f"{prefix}}}")
    return "\n".join(lines)


def _patch_event_body(event: Event, body: str) -> str:
    if event.touched:
        body = set_scalar(body, "id", event.id)
        body = set_scalar(body, "title", event.title or None)
        body = set_scalar(body, "desc", event.description or None)
        if any(span.key == "picture" for span in top_level_assignments(body)) or (
            event.picture and event.picture != "GFX_report_event_generic"
        ):
            body = set_scalar(body, "picture", event.picture or None)
        triggered_spans = [
            span for span in top_level_assignments(body) if span.key == "is_triggered_only"
        ]
        if triggered_spans or event.is_triggered_only:
            body = set_scalar(
                body, "is_triggered_only", "yes" if event.is_triggered_only else "no"
            )
        body = set_scalar(
            body,
            "fire_only_once",
            None if event.fire_only_once is None else ("yes" if event.fire_only_once else "no"),
        )
        body = set_block(body, "trigger", event.trigger or None)
        body = set_block(body, "immediate", event.immediate or None)
        body = set_block(body, "mean_time_to_happen", event.mean_time_to_happen or None)

    current_options = list(event.options)
    option_spans = [
        span
        for span in top_level_assignments(body)
        if span.key == "option" and span.is_block
    ]
    for index, span in reversed(list(enumerate(option_spans))):
        option = current_options[index] if index < len(current_options) else None
        if option is None:
            body = replace_assignment(body, span, None)
        elif span.body_start is not None and span.body_end is not None and option.raw_block:
            option_body = body[span.body_start : span.body_end]
            body = replace_assignment_body(body, span, _patch_option_body(option, option_body))
        else:
            body = replace_assignment(body, span, _serialize_option(option, indent=0))
    for option in current_options[len(option_spans) :]:
        body = append_assignment(body, _serialize_option(option, indent=0))
    return body


def _patch_option_body(option: EventOption, body: str) -> str:
    if not option.touched:
        return body
    original_effect = _option_effect(body)
    body = set_scalar(body, "name", option.name or None)
    body = set_block(body, "ai_chance", option.ai_chance or None)
    body = set_block(body, "trigger", option.trigger or None)
    if not block_bodies_equivalent(original_effect, option.effect):
        known = {"name", "ai_chance", "trigger"}
        for span in reversed(
            [span for span in top_level_assignments(body) if span.key not in known]
        ):
            body = replace_assignment(body, span, None)
        if option.effect:
            body = append_assignment(body, option.effect)
    return body


def _option_effect(block_text: str) -> str:
    trigger_body = _extract_block(block_text, "trigger")
    ai_body = _extract_block(block_text, "ai_chance")
    cleaned = block_text.strip()
    if trigger_body:
        trigger_match = find_assignment_block(cleaned, "trigger")
        if trigger_match:
            cleaned = (cleaned[: trigger_match[1]] + "\n" + cleaned[trigger_match[2] :]).strip()
    if ai_body:
        ai_match = find_assignment_block(cleaned, "ai_chance")
        if ai_match:
            cleaned = (cleaned[: ai_match[1]] + "\n" + cleaned[ai_match[2] :]).strip()
    lines = [
        line.strip()
        for line in cleaned.splitlines()
        if not re.match(r"^name\s*=", line.strip())
    ]
    return normalize_block_body("\n".join(lines).strip())


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(prefix + line.strip() for line in text.strip().splitlines())
