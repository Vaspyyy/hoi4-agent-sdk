"""
On-action system - read, create, and write HOI4 on_actions.

The SDK intentionally models on_actions conservatively: it preserves common
event lists and effect blocks, which is enough for startup scheduling hooks
without forcing agents to manually write common/on_actions files.
"""

from __future__ import annotations

from pathlib import Path

from .parser import find_assignment_block
from .script import normalize_block_body
from .types import OnAction


def load_on_actions_file(path: Path) -> list[OnAction]:
    txt = path.read_text(encoding="utf-8", errors="ignore")
    root = find_assignment_block(txt, "on_actions")
    body = root[0] if root else txt
    actions: list[OnAction] = []
    for action_id, action_body in _top_level_blocks(body):
        action = OnAction(
            id=action_id,
            effect=_extract_block(action_body, "effect"),
            events=_extract_list(action_body, "events"),
            random_events=_extract_list(action_body, "random_events"),
            path=path,
            raw_block=action_body.strip(),
        )
        actions.append(action)
    return actions


def serialize_on_action(action: OnAction) -> str:
    if action.raw_block:
        return f"\t{action.id} = {{\n{_indent(action.raw_block, 2)}\n\t}}"
    parts = [f"\t{action.id} = {{"]
    if action.events:
        parts.append("\t\tevents = {")
        for event_id in action.events:
            parts.append(f"\t\t\t{event_id}")
        parts.append("\t\t}")
    if action.random_events:
        parts.append("\t\trandom_events = {")
        for event_id in action.random_events:
            parts.append(f"\t\t\t{event_id}")
        parts.append("\t\t}")
    effect = normalize_block_body(action.effect)
    if effect:
        parts.append("\t\teffect = {")
        for line in effect.splitlines():
            if line.strip():
                parts.append(f"\t\t\t{line.strip()}")
        parts.append("\t\t}")
    parts.append("\t}")
    return "\n".join(parts)


def serialize_on_actions_file(actions: list[OnAction]) -> str:
    parts = ["on_actions = {"]
    for action in actions:
        parts.append(serialize_on_action(action))
        parts.append("")
    parts.append("}")
    parts.append("")
    return "\n".join(parts)


def write_on_actions_file(path: Path, actions: list[OnAction]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_on_actions_file(actions), encoding="utf-8")
    return path


def _extract_block(text: str, key: str) -> str:
    match = find_assignment_block(text, key)
    return match[0].strip() if match else ""


def _extract_list(text: str, key: str) -> list[str]:
    body = _extract_block(text, key)
    if not body:
        return []
    return [item for item in body.split() if item and not item.startswith("#")]


def _top_level_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    depth = 0
    in_quote = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and in_quote:
            i += 2
            continue
        if ch == '"':
            in_quote = not in_quote
            i += 1
            continue
        if ch == "#" and not in_quote:
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        if ch == "{" and not in_quote:
            depth += 1
            i += 1
            continue
        if ch == "}" and not in_quote:
            depth -= 1
            i += 1
            continue
        if depth == 0 and (ch.isalpha() or ch == "_"):
            start = i
            while i < len(text) and (text[i].isalnum() or text[i] == "_"):
                i += 1
            key = text[start:i]
            j = i
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] == "=":
                j += 1
                while j < len(text) and text[j].isspace():
                    j += 1
                if j < len(text) and text[j] == "{":
                    match = find_assignment_block(text, key, start)
                    if match and match[1] == start:
                        blocks.append((key, match[0]))
                        i = match[2]
                        continue
            continue
        i += 1
    return blocks


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(f"{prefix}{line.rstrip()}" for line in text.strip().splitlines())
