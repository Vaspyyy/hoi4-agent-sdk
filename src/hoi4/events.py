"""
Event system - read, create, and write HOI4 events.

Events live in events/*.txt files. Each file declares a namespace
and contains one or more country_event / state_event blocks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .parser import find_assignment_block, iter_assignment_blocks, strip_comments
from .types import Event, EventOption

NAMESPACE_RE = re.compile(r"add_namespace\s*=\s*(\S+)")
EVENT_TYPE_RE = re.compile(r"(country_event|state_event|news_event)\s*=\s*\{")
EVENT_ID_RE = re.compile(r"\bid\s*=\s*(\S+)")
TITLE_RE = re.compile(r"\btitle\s*=\s*(\S+)")
DESC_RE = re.compile(r"\bdesc\s*=\s*(\S+)")
PICTURE_RE = re.compile(r"\bpicture\s*=\s*(\S+)")
TRIGGERED_ONLY_RE = re.compile(r"\bis_triggered_only\s*=\s*(yes|no)")


def load_events_file(path: Path) -> tuple[Optional[str], list[Event]]:
    txt = path.read_text(encoding="utf-8", errors="ignore")

    ns_m = NAMESPACE_RE.search(strip_comments(txt))
    namespace = ns_m.group(1) if ns_m else None

    events: list[Event] = []
    event_blocks: list[tuple[int, str, str]] = []
    for event_type in ("country_event", "state_event", "news_event"):
        for chunk, start, _ in iter_assignment_blocks(txt, event_type):
            event_blocks.append((start, event_type, chunk))
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
    return match[0]


def _extract_options(chunk: str) -> list[EventOption]:
    options: list[EventOption] = []
    for block_text, _, _ in iter_assignment_blocks(chunk, "option"):
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
                before = cleaned[:trigger_match[1]].strip()
                after = cleaned[trigger_match[2]:].strip()
                cleaned = (before + "\n" + after).strip()
        ai_match = find_assignment_block(cleaned, "ai_chance")
        if ai_match:
            before = cleaned[:ai_match[1]].strip()
            after = cleaned[ai_match[2]:].strip()
            cleaned = (before + "\n" + after).strip()

        lines = []
        for line in cleaned.split("\n"):
            stripped = line.strip()
            if re.match(r"^name\s*=", stripped):
                continue
            lines.append(stripped)
        effect_text = "\n".join(lines).strip()
        if effect_text.startswith("{"):
            effect_text = effect_text[1:].strip()
        if effect_text.endswith("}"):
            effect_text = effect_text[:-1].strip()
        opt.effect = effect_text

        options.append(opt)

    return options


def serialize_event(event: Event) -> str:
    if event.raw_block:
        return f"{event.event_type} = {{\n{event.raw_block}\n}}"

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

    if event.trigger:
        parts.append("")
        parts.append("\ttrigger = {")
        for line in event.trigger.strip().split("\n"):
            parts.append(f"\t\t{line.strip()}")
        parts.append("\t}")

    if event.immediate:
        parts.append("")
        parts.append("\timmediate = {")
        for line in event.immediate.strip().split("\n"):
            parts.append(f"\t\t{line.strip()}")
        parts.append("\t}")

    if event.mean_time_to_happen:
        parts.append("")
        parts.append("\tmean_time_to_happen = {")
        for line in event.mean_time_to_happen.strip().split("\n"):
            parts.append(f"\t\t{line.strip()}")
        parts.append("\t}")

    for opt in event.options:
        parts.append("")
        parts.append("\toption = {")
        if opt.name:
            parts.append(f"\t\tname = {opt.name}")
        if opt.ai_chance:
            parts.append("\t\tai_chance = {")
            for line in opt.ai_chance.strip().split("\n"):
                parts.append(f"\t\t\t{line.strip()}")
            parts.append("\t\t}")
        if opt.trigger:
            parts.append("\t\ttrigger = {")
            for line in opt.trigger.strip().split("\n"):
                parts.append(f"\t\t\t{line.strip()}")
            parts.append("\t\t}")
        if opt.effect:
            for line in opt.effect.strip().split("\n"):
                parts.append(f"\t\t{line.strip()}")
        parts.append("\t}")

    parts.append("}")
    return "\n".join(parts)


def serialize_events_file(namespace: Optional[str], events: list[Event]) -> str:
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
