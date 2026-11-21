"""
Event system - read, create, and write HOI4 events.

Events live in events/*.txt files. Each file declares a namespace and contains
one or more supported event blocks.

Reading is a query of the syntax tree in :mod:`hoi4.syntax`. Writing compares
the model with what the event's source text says and replaces only the spans
of the fields that differ, so comments, unknown keys, repeated blocks and the
formatting of everything else stay byte-identical.

Titles, descriptions and option names come in two shapes::

    title = KEY                                     # unconditional
    title = { text = KEY trigger = { ... } }        # conditional

The unconditional key is the plain field (``Event.title``, ``Event.description``,
``EventOption.name``). Entries with a trigger are the ``conditional_*`` lists,
in source order. A block without a trigger (``title = { text = KEY }``) counts
as unconditional.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .patching import (
    AssignmentSpan,
    append_assignment,
    dedent_block_body,
    one_statement_per_line,
    replace_assignment,
    replace_assignment_body,
    same_block_body,
    scalar_values_equivalent,
    set_block,
    set_scalar,
    statement_spans,
    sync_body,
)
from .script import normalize_block_body
from .syntax import Assignment, Block, Scalar, Tagged, parse, quote, require_well_formed
from .textio import read_text, write_text
from .types import Event, EventOption, EventText

VALID_EVENT_TYPES = frozenset(
    {
        "country_event",
        "state_event",
        "news_event",
        "unit_leader_event",
        "operative_leader_event",
    }
)

_OPTION_KNOWN_KEYS = frozenset({"name", "ai_chance", "trigger"})


# ── reading ─────────────────────────────────────────────────────


def scan_event_ids_file(path: Path) -> set[str]:
    """Extract event IDs without constructing full event/option models."""

    ids: set[str] = set()
    for item in parse(read_text(path)).root.assignments():
        block = item.block
        if block is None or not (item.name in VALID_EVENT_TYPES or item.name.endswith("_event")):
            continue
        event_id = block.scalar("id")
        if event_id:
            ids.add(event_id)
    return ids


def load_events_file(path: Path) -> tuple[Optional[str], list[Event]]:
    doc = parse(read_text(path))
    namespace = doc.root.scalar("add_namespace")
    events: list[Event] = []
    for item in doc.root.assignments():
        block = item.block
        if item.name not in VALID_EVENT_TYPES or block is None or item.op != "=":
            continue
        event = _event_from_block(block, item.name, path)
        if event is not None:
            events.append(event)
    return namespace or None, events


def _event_from_block(block: Block, event_type: str, path: Optional[Path]) -> Optional[Event]:
    event_id = block.scalar("id")
    if not event_id:
        return None
    event = Event(id=event_id, event_type=event_type, path=path, raw_block=block.body.strip())
    event.title, event.conditional_titles = _read_text_entries(block, "title")
    event.description, event.conditional_descriptions = _read_text_entries(block, "desc")
    picture = block.scalar("picture")
    if picture is not None:
        event.picture = picture
    event.is_triggered_only = block.scalar("is_triggered_only") == "yes"
    event.fire_only_once = _read_bool(block, "fire_only_once")
    event.trigger = _block_body(block, "trigger")
    event.immediate = _block_body(block, "immediate")
    event.mean_time_to_happen = _block_body(block, "mean_time_to_happen")
    event.options = [_option_from_block(option) for option in block.blocks("option")]
    return event


def _option_from_block(block: Block) -> EventOption:
    body = block.body
    option = EventOption(raw_block=body.strip(), _source_body=body)
    option.name, option.conditional_names = _read_text_entries(block, "name")
    option.ai_chance = _block_body(block, "ai_chance")
    option.trigger = _block_body(block, "trigger")
    option.effect = _option_effect(body)
    return option


def _parse_body(body: str) -> Block:
    return parse(body).root


def _read_bool(block: Block, key: str) -> Optional[bool]:
    value = block.scalar(key)
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


def _block_body(block: Block, key: str) -> str:
    """The dedented body of the first top-level ``key = { ... }``, or ``""``."""

    for item in block.assignments(key):
        if item.op == "=" and type(item.value) is Block:
            return dedent_block_body(item.value.body)
    return ""


def _text_entries(block: Block, key: str) -> list[Assignment]:
    return [
        item
        for item in block.assignments(key)
        if item.op == "=" and type(item.value) in (Scalar, Block)
    ]


def _entry_trigger(item: Assignment) -> Optional[Block]:
    value = item.value
    if type(value) is not Block:
        return None
    for trigger in value.assignments("trigger"):
        if type(trigger.value) is Block:
            return trigger.value
    return None


def _entry_text(item: Assignment) -> str:
    value = item.value
    if type(value) is Scalar:
        return value.value
    if type(value) is Block:
        return value.scalar("text") or ""
    return ""


def _read_text_entries(block: Block, key: str) -> tuple[str, list[EventText]]:
    """Return the unconditional key and the conditional entries of ``key``."""

    plain: Optional[str] = None
    conditional: list[EventText] = []
    for item in _text_entries(block, key):
        trigger = _entry_trigger(item)
        if trigger is None:
            if plain is None:
                plain = _entry_text(item)
        else:
            conditional.append(EventText(_entry_text(item), dedent_block_body(trigger.body)))
    return plain or "", conditional


def _option_effect(body: str) -> str:
    """The option's script without its ``name``, ``ai_chance`` and ``trigger``."""

    spans = statement_spans(body, keep=_OPTION_KNOWN_KEYS)
    if spans is None:
        # Malformed body: everything that is not one of the known statements.
        known = [
            _span(item)
            for item in _parse_body(body).items
            if type(item) is Assignment and item.name in _OPTION_KNOWN_KEYS
        ]
        for span in reversed(known):
            body = replace_assignment(body, span, None)
        pieces = [body]
    else:
        # The effect statements with their comments. A comment on the line of
        # ``name``, ``ai_chance`` or ``trigger`` belongs to that statement.
        pieces = [body[start:end] for start, end in spans]
    lines = [line.strip() for piece in pieces for line in piece.strip().splitlines()]
    return normalize_block_body("\n".join(lines).strip())


def _span(item: Assignment | Scalar | Block | Tagged) -> AssignmentSpan:
    if type(item) is not Assignment:
        return AssignmentSpan("", item.start, item.end, item.start, item.end, False)
    value = item.value
    block = item.block
    if block is not None:
        return AssignmentSpan(
            item.name, item.start, item.end, value.start, value.end, True,
            block.body_start, block.body_end, item.op,
        )
    return AssignmentSpan(item.name, item.start, item.end, value.start, value.end, False,
                          operator=item.op)


# ── rendering new script ────────────────────────────────────────


def _render_text_entry(key: str, entry: EventText) -> str:
    trigger = normalize_block_body(entry.trigger)
    # A trigger with a stray or missing brace would change the script around it.
    require_well_formed(trigger, f"the trigger of a conditional '{key}'")
    text = quote(entry.text)
    if not trigger:
        return f"{key} = {{ text = {text} }}"
    if "\n" not in trigger and "#" not in trigger:
        return f"{key} = {{ text = {text} trigger = {{ {trigger} }} }}"
    inner = "\n".join("\t\t" + line.strip() for line in trigger.splitlines())
    return f"{key} = {{\n\ttext = {text}\n\ttrigger = {{\n{inner}\n\t}}\n}}"


def _render_text_entries(key: str, plain: str, conditional: list[EventText]) -> list[str]:
    lines: list[str] = []
    for entry in conditional:
        lines.extend(_render_text_entry(key, entry).splitlines())
    if plain:
        lines.append(f"{key} = {quote(plain)}")
    return lines


def serialize_event(event: Event) -> str:
    if event.raw_block:
        body = _patch_event_body(event, event.raw_block)
        return f"{event.event_type} = {{\n{_indent(body, 1)}\n}}"

    parts: list[str] = []
    parts.append(f"{event.event_type} = {{")
    parts.append(f"\tid = {event.id}")

    for line in _render_text_entries("title", event.title, event.conditional_titles):
        parts.append(f"\t{line}")
    for line in _render_text_entries("desc", event.description, event.conditional_descriptions):
        parts.append(f"\t{line}")
    parts.append(f"\tpicture = {quote(event.picture)}")

    if event.is_triggered_only:
        parts.append("\tis_triggered_only = yes")
    if event.fire_only_once is not None:
        parts.append(f"\tfire_only_once = {'yes' if event.fire_only_once else 'no'}")

    for key, value in (
        ("trigger", event.trigger),
        ("immediate", event.immediate),
        ("mean_time_to_happen", event.mean_time_to_happen),
    ):
        body = normalize_block_body(value)
        if body:
            require_well_formed(body, f"the {key} of event '{event.id}'")
            parts.append("")
            parts.append(f"\t{key} = {{")
            for line in body.split("\n"):
                parts.append(f"\t\t{line.strip()}")
            parts.append("\t}")

    for opt in event.options:
        parts.append("")
        parts.extend(_serialize_option(opt, indent=1).splitlines())

    parts.append("}")
    return "\n".join(parts)


def serialize_events_file(namespace: Optional[str], events: list[Event], original: str = "") -> str:
    if original:
        text = original
        if parse(text).root.scalar("add_namespace") != namespace:
            text = set_scalar(text, "add_namespace", namespace)
        current = {event.id: event for event in events}
        spans: list[tuple[str, AssignmentSpan]] = []
        for item in parse(text).root.assignments():
            block = item.block
            if item.name not in VALID_EVENT_TYPES or block is None or item.op != "=":
                continue
            event_id = block.scalar("id")
            if event_id:
                spans.append((event_id, _span(item)))
        for event_id, span in reversed(spans):
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
    write_text(path, serialize_events_file(namespace, events))
    return path


def _serialize_option(option: EventOption, indent: int) -> str:
    prefix = "\t" * indent
    if option.raw_block:
        body = _patch_option_body(option, option.raw_block, prefix)
        return f"{prefix}option = {{\n{_indent(body, indent + 1)}\n{prefix}}}"
    lines = [f"{prefix}option = {{"]
    for line in _render_text_entries("name", option.name, option.conditional_names):
        lines.append(f"{prefix}\t{line}")
    for key, value in [("ai_chance", option.ai_chance), ("trigger", option.trigger)]:
        if value:
            require_well_formed(value, f"the {key} of an event option")
            lines.append(f"{prefix}\t{key} = {{")
            lines.extend(
                f"{prefix}\t\t{line.strip()}" for line in normalize_block_body(value).splitlines()
            )
            lines.append(f"{prefix}\t}}")
    if option.effect:
        require_well_formed(option.effect, "the effect of an event option")
        lines.extend(f"{prefix}\t{line.strip()}" for line in option.effect.strip().splitlines())
    lines.append(f"{prefix}}}")
    return "\n".join(lines)


# ── patching source text ────────────────────────────────────────


def _patch_event_body(event: Event, body: str) -> str:
    """Return ``body`` with exactly the fields that differ from the model replaced."""

    source = _event_from_block(_parse_body(body), event.event_type, None)
    if source is None:
        source = Event(id=event.id)
    body = _patch_text_entries(body, "title", event.title, event.conditional_titles, ("id",))
    body = _patch_text_entries(
        body, "desc", event.description, event.conditional_descriptions, ("title", "id")
    )
    anchors: tuple[str, ...] = ("desc", "title", "id")
    if event.picture != source.picture:
        body = _set_scalar_near(
            body, "picture", quote(event.picture) if event.picture else None, anchors
        )
    anchors = ("picture", *anchors)
    if event.is_triggered_only != source.is_triggered_only:
        body = _set_scalar_near(
            body, "is_triggered_only", "yes" if event.is_triggered_only else "no", anchors
        )
    anchors = ("is_triggered_only", *anchors)
    if event.fire_only_once != source.fire_only_once:
        value = None if event.fire_only_once is None else ("yes" if event.fire_only_once else "no")
        body = _set_scalar_near(body, "fire_only_once", value, anchors)
    for key, value, old in (
        ("trigger", event.trigger, source.trigger),
        ("immediate", event.immediate, source.immediate),
        ("mean_time_to_happen", event.mean_time_to_happen, source.mean_time_to_happen),
    ):
        if not same_block_body(old, value):
            body = _set_block_before_options(body, key, value or None)
    return _patch_options(event.options, body)


def _patch_options(options: list[EventOption], body: str) -> str:
    option_spans = [
        _span(item)
        for item in _parse_body(body).assignments("option")
        if item.op == "=" and type(item.value) is Block
    ]
    for index, span in reversed(list(enumerate(option_spans))):
        option = options[index] if index < len(options) else None
        if option is None:
            body = replace_assignment(body, span, None)
        elif (
            span.body_start is not None
            and span.body_end is not None
            and (option.raw_block or option._source_body is not None)
        ):
            # The destination slot is only a location, never the option's identity.
            # New options have no source body and cannot claim a loaded occurrence.
            option_body = (
                option._source_body if option._source_body is not None else option.raw_block
            )
            body = replace_assignment_body(
                body, span, _patch_option_body(option, option_body, _line_indent(body, span.start))
            )
        else:
            body = replace_assignment(body, span, _serialize_option(option, indent=0))
    for option in options[len(option_spans) :]:
        # A leading indent prevents append_assignment from reindenting every
        # line of a retained source body when it moves beyond the old slots.
        if option._source_body is not None:
            option_body = _patch_option_body(option, option._source_body, "\t")
            rendered = f"\toption = {{{option_body}}}"
        else:
            rendered = _serialize_option(option, indent=0)
        body = append_assignment(body, rendered)
    return body


def _patch_option_body(option: EventOption, body: str, indent: str) -> str:
    """Write the fields of ``option`` that differ from what ``body`` says.

    ``indent`` is the indentation of the line that holds ``option = {``.
    """

    source = _option_from_block(_parse_body(body))
    body = _patch_text_entries(body, "name", option.name, option.conditional_names, ())
    if not same_block_body(source.ai_chance, option.ai_chance):
        body = set_block(body, "ai_chance", option.ai_chance or None)
    if not same_block_body(source.trigger, option.trigger):
        body = set_block(body, "trigger", option.trigger or None)
    if not same_block_body(source.effect, option.effect):
        body = _set_option_effect(body, option.effect, indent)
    comments = parse(body).comments if "#" in body else []
    if comments and "\n" not in body[comments[-1][0] :]:
        # A comment must never reach the closing brace of the option.
        body = body.rstrip(" \t") + "\n" + indent
    return body


def _set_option_effect(body: str, effect: str, indent: str) -> str:
    """Change the effect statement by statement.

    The effect is every statement and comment of the option that is not one
    of the known keys.
    """

    # An effect with a stray brace would end the option and start other script.
    require_well_formed(effect, "the effect of an event option")
    synced = sync_body(body, effect, keep=_OPTION_KNOWN_KEYS)
    if synced is not None:
        return synced
    # The layout cannot hold the edit (a one-line option cannot hold a comment
    # or a statement of several lines): give every statement its own line,
    # then try again.
    spread = one_statement_per_line(body, indent)
    if spread is not None and spread != body:
        body = spread
        synced = sync_body(body, effect, keep=_OPTION_KNOWN_KEYS)
        if synced is not None:
            return synced
    return _replace_option_effect(body, effect)


def _line_indent(text: str, offset: int) -> str:
    """The leading whitespace of the line that holds ``offset``."""

    line = text[text.rfind("\n", 0, offset) + 1 :]
    return line[: len(line) - len(line.lstrip(" \t"))]


def _replace_option_effect(body: str, effect: str) -> str:
    """Cut the whole effect (statements and their comments) and write the new one."""

    spans = statement_spans(body, keep=_OPTION_KNOWN_KEYS)
    if spans is None:
        spans = [
            (item.start, item.end)
            for item in _parse_body(body).items
            if not (type(item) is Assignment and item.name in _OPTION_KNOWN_KEYS)
        ]
    for start, end in reversed(spans):
        body = replace_assignment(body, AssignmentSpan("", start, end, start, end, False), None)
    return append_assignment(body, effect) if effect else body


def _text_entries_equal(left: list[EventText], right: list[EventText]) -> bool:
    return len(left) == len(right) and all(
        _text_entry_equal(a, b) for a, b in zip(left, right)
    )


def _text_entry_equal(left: EventText, right: EventText) -> bool:
    return left.text == right.text and same_block_body(right.trigger, left.trigger)


def _patch_text_entries(
    body: str,
    key: str,
    plain: str,
    conditional: list[EventText],
    anchors: tuple[str, ...],
) -> str:
    """Bring the ``key`` entries of ``body`` in line with ``plain`` and ``conditional``.

    Only entries that differ are touched. A changed key or trigger is replaced
    inside its own entry; removed entries are deleted; new conditional entries
    go after the last conditional one (or before the unconditional one), and a
    new unconditional key goes after every existing entry, where the game falls
    back to it.
    """

    source_plain, source_conditional = _read_text_entries(_parse_body(body), key)

    if plain != source_plain:
        entries = _text_entries(_parse_body(body), key)
        unconditional = [item for item in entries if _entry_trigger(item) is None]
        if not plain:
            for item in reversed(unconditional):
                body = replace_assignment(body, _span(item), None)
        elif unconditional:
            body = _set_entry_text(body, unconditional[0], plain)
        else:
            body = _insert_statement(
                body, f"{key} = {quote(plain)}", after=_last_of(body, (key, *anchors))
            )

    if not _text_entries_equal(conditional, source_conditional):
        entries = [
            item for item in _text_entries(_parse_body(body), key)
            if _entry_trigger(item) is not None
        ]
        for index in reversed(range(len(entries))):
            item = entries[index]
            if index >= len(conditional):
                body = replace_assignment(body, _span(item), None)
            elif not _text_entry_equal(conditional[index], source_conditional[index]):
                body = _patch_conditional_entry(body, item, conditional[index])
        for entry in conditional[len(entries) :]:
            block = _parse_body(body)
            current = _text_entries(block, key)
            conditional_items = [item for item in current if _entry_trigger(item) is not None]
            rendered = _render_text_entry(key, entry)
            if conditional_items:
                body = _insert_statement(body, rendered, after=conditional_items[-1])
            elif current:
                body = _insert_statement(body, rendered, before=current[0])
            else:
                body = _insert_statement(body, rendered, after=_last_of(body, anchors))
    return body


def _set_entry_text(body: str, item: Assignment, text: str) -> str:
    value = item.value
    if type(value) is Scalar:
        if scalar_values_equivalent(value.raw, quote(text)):
            return body
        return body[: value.start] + quote(text) + body[value.end :]
    span = _span(item)
    assert span.body_start is not None and span.body_end is not None
    inner = body[span.body_start : span.body_end]
    return replace_assignment_body(body, span, set_scalar(inner, "text", quote(text)))


def _patch_conditional_entry(body: str, item: Assignment, entry: EventText) -> str:
    span = _span(item)
    assert span.body_start is not None and span.body_end is not None
    inner = body[span.body_start : span.body_end]
    old_text = _entry_text(item)
    if entry.text != old_text:
        inner = set_scalar(inner, "text", quote(entry.text))
    trigger = _entry_trigger(item)
    old_trigger = dedent_block_body(trigger.body) if trigger is not None else ""
    if not same_block_body(old_trigger, entry.trigger):
        inner = set_block(inner, "trigger", normalize_block_body(entry.trigger) or None)
    return replace_assignment_body(body, span, inner)


def _last_of(body: str, keys: tuple[str, ...]) -> Optional[Assignment]:
    """The last top-level assignment whose key is in ``keys``, or ``None``."""

    found: Optional[Assignment] = None
    for item in _parse_body(body).assignments():
        if item.name in keys:
            found = item
    return found


def _set_scalar_near(
    body: str, key: str, value: Optional[str], anchors: tuple[str, ...]
) -> str:
    """Set ``key = value``; a new statement goes after the last of ``anchors``."""

    if value is None or _parse_body(body).first(key) is not None:
        return set_scalar(body, key, value)
    return _insert_statement(body, f"{key} = {value}", after=_last_of(body, anchors))


def _set_block_before_options(body: str, key: str, value: Optional[str]) -> str:
    """Set ``key = { value }``; a new block goes before the first option."""

    block = _parse_body(body)
    first_option = block.first("option")
    if value is None or block.first(key) is not None or first_option is None:
        return set_block(body, key, value)
    cleaned = normalize_block_body(value)
    require_well_formed(cleaned, f"the new body of '{key}'")
    inner = "\n".join("\t" + line.strip() for line in cleaned.splitlines())
    return _insert_statement(body, f"{key} = {{\n{inner}\n}}", before=first_option)


def _insert_statement(
    body: str,
    statement: str,
    *,
    after: Optional[Assignment | Scalar | Block | Tagged] = None,
    before: Optional[Assignment | Scalar | Block | Tagged] = None,
) -> str:
    """Insert ``statement`` next to an existing item, matching its indentation.

    When the item owns its line, the statement gets a line of its own with the
    same indentation. Otherwise (``a = 1 b = 2`` on one line) it is placed on
    that line, separated by a space.
    """

    if after is None and before is None:
        items = _parse_body(body).items
        if not items:
            return append_assignment(body, statement)
        before = items[0]
    anchor = after if after is not None else before
    assert anchor is not None
    line_start = body.rfind("\n", 0, anchor.start) + 1
    prefix = body[line_start : anchor.start]
    # On the first line of a body the item shares its line with the ``{``.
    owns_line = line_start > 0 and not prefix.strip()
    indent = prefix if owns_line else ""
    lines = statement.splitlines()
    if after is not None:
        line_end = body.find("\n", anchor.end)
        rest = body[anchor.end : len(body) if line_end < 0 else line_end]
        commented = rest.lstrip().startswith("#")
        rest_is_trailer = not rest.strip() or commented
        if commented and not owns_line and line_end >= 0:
            # ``a = 1 b = 2 # note``: the comment stays with the statement it
            # follows, and the new one goes on a line of its own below. On the
            # line of the opening brace it is indented like the line after it.
            owns_line = True
            below = body[line_end + 1 :].split("\n", 1)[0]
            source = prefix if line_start > 0 else below if below.strip() else "\t"
            indent = source[: len(source) - len(source.lstrip())]
        # On the last line of a body the item shares its line with the ``}``.
        if owns_line and rest_is_trailer and line_end >= 0:
            rendered = "".join(indent + line + "\n" for line in lines)
            return body[: line_end + 1] + rendered + body[line_end + 1 :]
        rendered = " " + "\n".join(lines)
        return body[: anchor.end] + rendered + body[anchor.end :]
    if owns_line:
        rendered = "".join(indent + line + "\n" for line in lines)
        return body[:line_start] + rendered + body[line_start:]
    return body[: anchor.start] + "\n".join(lines) + " " + body[anchor.start :]


def _indent(text: str, count: int) -> str:
    prefix = "\t" * count
    return "\n".join(prefix + line.strip() for line in text.strip().splitlines())
