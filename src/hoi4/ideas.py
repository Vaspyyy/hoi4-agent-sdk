"""
Ideas / National Spirits - read, create, and write HOI4 ideas.

Idea files live in common/national_ideas/*.txt and common/ideas/*.txt.
Each file contains a country_ideas block or ideas block with idea definitions.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .parser import extract_braced_block, find_assignment_block, iter_assignment_blocks, strip_comments
from .types import Idea

IDEA_BLOCK_RE = re.compile(r"\b([a-zA-Z0-9_]+)\s*=\s*\{")
KV_RE = re.compile(r"^\s*([a-zA-Z0-9_]+)\s*=\s*([^\n\r]+)", re.MULTILINE)
PICTURE_RE = re.compile(r"\bpicture\s*=\s*([^\n\r]+)")
ICON_RE = re.compile(r"\bicon\s*=\s*([^\n\r]+)")


VALID_CONTAINERS = ("country_ideas", "ideas")


def _extract_block(text: str, keyword: str) -> str:
    match = find_assignment_block(text, keyword)
    if not match:
        return ""
    return match[0]


def detect_ideas_container(text: str) -> str:
    for name in VALID_CONTAINERS:
        if re.search(rf"\b{re.escape(name)}\s*=\s*\{{", text):
            return name
    return "country_ideas"


def _find_toplevel_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        m = IDEA_BLOCK_RE.search(text[pos:])
        if not m:
            break
        block_id = m.group(1)
        block_start = pos + m.end()
        try:
            block_content, block_end = extract_braced_block(text, block_start)
            blocks.append((block_id, block_content))
            pos = block_end
        except ValueError:
            pos = block_start
    return blocks


def _has_direct_property(text: str, keys: set[str]) -> bool:
    clean = strip_comments(text)
    depth = 0
    token = []
    i = 0
    while i < len(clean):
        c = clean[i]
        if c == '"':
            i += 1
            while i < len(clean) and clean[i] != '"':
                if clean[i] == "\\":
                    i += 1
                i += 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 0 and (c.isalnum() or c == "_"):
            start = i
            while i < len(clean) and (clean[i].isalnum() or clean[i] == "_"):
                i += 1
            key = clean[start:i]
            j = i
            while j < len(clean) and clean[j].isspace():
                j += 1
            if key in keys and j < len(clean) and clean[j] == "=":
                return True
            continue
        i += 1
    return False


def _is_idea_body(text: str) -> bool:
    return _has_direct_property(text, {
        "icon",
        "picture",
        "modifier",
        "research_bonus",
        "traits",
        "allowed",
        "allowed_civil_war",
        "ai_will_do",
        "cost",
        "visible",
    })


def _parse_kv(text: str) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for key, value in KV_RE.findall(text):
        value = value.strip().strip('"')
        if value.lower() in ("yes", "no"):
            props[key.strip()] = value.lower() == "yes"
        elif "." in value:
            try:
                props[key.strip()] = float(value)
            except ValueError:
                props[key.strip()] = value
        else:
            try:
                props[key.strip()] = int(value)
            except ValueError:
                props[key.strip()] = value
    return props


def _parse_idea_body(idea_id: str, body: str) -> Idea:
    idea = Idea(id=idea_id.strip())
    pic = PICTURE_RE.search(body) or ICON_RE.search(body)
    if pic:
        idea.icon = pic.group(1).strip()
    modifier_body = _extract_block(body, "modifier")
    if modifier_body:
        idea.modifier = _parse_kv(modifier_body)
    allowed_body = _extract_block(body, "allowed")
    if allowed_body:
        idea.allowed = allowed_body.strip()
    research_body = _extract_block(body, "research_bonus")
    if research_body:
        idea.research_bonus = _parse_kv(research_body)
    traits_body = _extract_block(body, "traits")
    if traits_body:
        idea.traits = [item for item in strip_comments(traits_body).split() if item]
    ai_body = _extract_block(body, "ai_will_do")
    if ai_body:
        idea.ai_will_do = ai_body.strip()
    idea.raw_block = body.strip()
    return idea


def read_ideas_file(path: Path) -> tuple[list[Idea], str]:
    txt = path.read_text(encoding="utf-8", errors="ignore")
    container_name = detect_ideas_container(txt)
    container = _extract_block(txt, container_name)
    if not container:
        return [], container_name
    ideas: list[Idea] = []
    for block_id, block_body in _find_toplevel_blocks(container):
        if _is_idea_body(block_body):
            idea = _parse_idea_body(block_id, block_body)
            idea.path = path
            ideas.append(idea)
            continue
        for idea_id, idea_body in _find_toplevel_blocks(block_body):
            idea = _parse_idea_body(idea_id, idea_body)
            idea.category = block_id
            idea.path = path
            ideas.append(idea)
    return ideas, container_name


def serialize_idea(idea: Idea, indent: int = 1) -> str:
    tab = "\t" * indent
    lines = [f"{tab}{idea.id} = {{"]
    lines.append(f"{tab}\ticon = {idea.icon}")
    if idea.allowed:
        lines.append(f"{tab}\tallowed = {{")
        for line in idea.allowed.strip().split("\n"):
            lines.append(f"{tab}\t\t{line.strip()}")
        lines.append(f"{tab}\t}}")
    if idea.modifier:
        lines.append(f"{tab}\tmodifier = {{")
        for key, value in idea.modifier.items():
            if isinstance(value, bool):
                lines.append(f"{tab}\t\t{key} = {'yes' if value else 'no'}")
            elif isinstance(value, float):
                lines.append(f"{tab}\t\t{key} = {value}")
            elif isinstance(value, int):
                lines.append(f"{tab}\t\t{key} = {value}")
            else:
                lines.append(f'{tab}\t\t{key} = "{value}"')
        lines.append(f"{tab}\t}}")
    if idea.research_bonus:
        lines.append(f"{tab}\tresearch_bonus = {{")
        for key, value in idea.research_bonus.items():
            lines.append(f"{tab}\t\t{key} = {value}")
        lines.append(f"{tab}\t}}")
    if idea.ai_will_do:
        lines.append(f"{tab}\tai_will_do = {{")
        for line in idea.ai_will_do.strip().split("\n"):
            lines.append(f"{tab}\t\t{line.strip()}")
        lines.append(f"{tab}\t}}")
    if idea.traits:
        lines.append(f"{tab}\ttraits = {{ {' '.join(idea.traits)} }}")
    lines.append(f"{tab}}}")
    return "\n".join(lines)


def serialize_ideas_file(ideas: list[Idea], container_name: str = "country_ideas") -> str:
    parts = [f"{container_name} = {{"]
    emitted_categories: set[str] = set()
    for idea in ideas:
        if not idea.category:
            parts.append(serialize_idea(idea))
            parts.append("")
            continue
        if idea.category in emitted_categories:
            continue
        emitted_categories.add(idea.category)
        parts.append(f"\t{idea.category} = {{")
        for nested in [candidate for candidate in ideas if candidate.category == idea.category]:
            parts.append(serialize_idea(nested, indent=2))
            parts.append("")
        parts.append("\t}")
        parts.append("")
    parts.append("}")
    parts.append("")
    return "\n".join(parts)


def write_ideas_file(path: Path, ideas: list[Idea], container_name: str = "country_ideas") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_ideas_file(ideas, container_name=container_name)
    path.write_text(content, encoding="utf-8")
    return path


def read_assigned_ideas(history_file: Path) -> list[str]:
    if not history_file.exists():
        return []
    text = history_file.read_text(encoding="utf-8", errors="ignore")
    assigned: list[str] = []
    removed: set[str] = set()

    for m in re.finditer(r"add_ideas\s*=\s*\{", text):
        start = m.end()
        try:
            block, _ = extract_braced_block(text, start)
        except ValueError:
            continue
        for im in re.finditer(r"\b([a-zA-Z][a-zA-Z0-9_]*)\b", block):
            word = im.group(1)
            if word.lower() not in ("yes", "no", "always", "and", "or", "not", "tag"):
                assigned.append(word)

    for m in re.finditer(r"remove_ideas\s*=\s*\{", text):
        start = m.end()
        try:
            block, _ = extract_braced_block(text, start)
        except ValueError:
            continue
        for im in re.finditer(r"\b([a-zA-Z][a-zA-Z0-9_]*)\b", block):
            removed.add(im.group(1))

    for m in re.finditer(r"remove_ideas\s*=\s*([a-zA-Z][a-zA-Z0-9_]*)", text):
        removed.add(m.group(1))

    return [idea for idea in dict.fromkeys(assigned) if idea not in removed]
