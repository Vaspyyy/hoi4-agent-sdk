"""Event authoring."""

from __future__ import annotations

from ..paths import (
    require_event_namespace,
    require_script_id,
    resolve_mod_output_path,
)
from ..script import (
    normalize_block_body,
)
from ..types import (
    Event,
    EventOption,
    EventText,
)
from ._core import CoreLayer
from ._shared import (
    _set_fields,
)


def _infer_event_namespace(event_id: str) -> str | None:
    if "." not in event_id:
        return None
    namespace = event_id.split(".", 1)[0]
    if namespace.replace("_", "").isalnum() and not namespace[0].isdigit():
        return namespace
    return None


def _normalize_event_option(option: EventOption) -> EventOption:
    option.trigger = normalize_block_body(option.trigger)
    option.ai_chance = normalize_block_body(option.ai_chance)
    return option


# Setting the plain key through the facade replaces every alternative, unless
# the caller passes the conditional list too.
_EVENT_TEXT_LISTS = {"title": "conditional_titles", "description": "conditional_descriptions"}


def _replace_conditional_texts(kwargs: dict, lists: dict[str, str]) -> None:
    for plain, conditional in lists.items():
        if plain in kwargs and conditional not in kwargs:
            kwargs[conditional] = []
    for conditional in lists.values():
        if conditional in kwargs:
            kwargs[conditional] = [_event_text(entry) for entry in kwargs[conditional] or ()]


def _event_text(entry: EventText | tuple[str, str] | dict) -> EventText:
    if isinstance(entry, EventText):
        return EventText(entry.text, normalize_block_body(entry.trigger))
    if isinstance(entry, dict):
        return EventText(str(entry["text"]), normalize_block_body(str(entry.get("trigger", ""))))
    text, trigger = entry
    return EventText(text, normalize_block_body(trigger))


class EventsLayer(CoreLayer):
    """Event authoring."""

    # ── Events ──────────────────────────────────────────────────

    def list_events(self) -> list[str]:
        return sorted(self._events.keys())

    def get_event(self, event_id: str) -> Event:
        if event_id not in self._events:
            raise KeyError(f"Event '{event_id}' not found. Available: {self.list_events()}")
        return self._events[event_id]

    def create_event(
        self,
        event_id: str,
        title: str = "",
        description: str = "",
        event_type: str = "country_event",
        picture: str = "GFX_report_event_generic",
        is_triggered_only: bool = False,
        fire_only_once: bool | None = None,
        trigger: str = "",
        immediate: str = "",
        mean_time_to_happen: str = "",
        options: list[EventOption] | None = None,
        overwrite: bool = False,
        conditional_titles: list[EventText] | None = None,
        conditional_descriptions: list[EventText] | None = None,
    ) -> Event:
        """Create an event.

        ``conditional_titles``/``conditional_descriptions`` add
        ``title = { text = ... trigger = { ... } }`` entries, shown before the
        plain ``title``/``description`` fallback.
        """
        event_id = require_script_id(event_id, label="event ID")
        existing = self._events.get(event_id)
        if existing is not None and not overwrite:
            raise ValueError(
                f"Event '{event_id}' already exists. Use overwrite=True to replace it, "
                "or update_event()/update_event_option() to patch it."
            )
        existing_namespace = self._event_namespaces.get(event_id)
        event = Event(
            id=event_id,
            title=title or f"{event_id}.t",
            description=description or f"{event_id}.d",
            event_type=event_type,
            picture=picture,
            is_triggered_only=is_triggered_only,
            fire_only_once=fire_only_once,
            trigger=normalize_block_body(trigger),
            immediate=normalize_block_body(immediate),
            mean_time_to_happen=normalize_block_body(mean_time_to_happen),
            options=[
                _normalize_event_option(option)
                for option in (options or [EventOption(name=f"{event_id}.a", effect="")])
            ],
            path=existing.path if existing is not None else None,
            touched=True,
            conditional_titles=[_event_text(entry) for entry in conditional_titles or ()],
            conditional_descriptions=[
                _event_text(entry) for entry in conditional_descriptions or ()
            ],
        )
        self._events[event_id] = event
        self._event_namespaces[event_id] = (
            existing_namespace
            if existing_namespace is not None
            else _infer_event_namespace(event_id)
        )
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return event

    def ensure_event(self, event_id: str, **kwargs) -> Event:
        """Create an event if missing, otherwise patch the existing event."""
        if event_id in self._events:
            self.update_event(event_id, **kwargs)
            return self._events[event_id]
        return self.create_event(event_id, **kwargs)

    def update_event(self, event_id: str, **kwargs) -> bool:
        """Patch fields of an event; only the changed fields are rewritten on save.

        Passing ``title`` (or ``description``) without ``conditional_titles``
        (or ``conditional_descriptions``) replaces every conditional
        alternative with the one plain key. Pass both to keep or change them.
        """
        event = self._events.get(event_id)
        if event is None:
            return False
        if "id" in kwargs:
            raise ValueError("Event IDs are immutable; create a new event instead")
        old_path = (
            event.path
            or self.mod_root
            / "events"
            / f"{self._event_namespaces.get(event_id) or 'mod'}_events.txt"
        )
        if "path" in kwargs:
            kwargs["path"] = resolve_mod_output_path(self.mod_root, kwargs["path"])
        for key in ("trigger", "immediate", "mean_time_to_happen"):
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = normalize_block_body(kwargs[key])
        if "options" in kwargs and isinstance(kwargs["options"], list):
            kwargs["options"] = [_normalize_event_option(option) for option in kwargs["options"]]
        _replace_conditional_texts(kwargs, _EVENT_TEXT_LISTS)
        _set_fields(event, kwargs, allow_path=True)
        event.touched_fields.update(kwargs)
        event.touched = True
        self._dirty_event_files.add(old_path)
        self._dirty_event_files.add(
            event.path
            or self.mod_root
            / "events"
            / f"{self._event_namespaces.get(event_id) or 'mod'}_events.txt"
        )
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return True

    def delete_event(self, event_id: str) -> bool:
        event = self._events.get(event_id)
        if event is None:
            return False
        old_path = (
            event.path
            or self.mod_root
            / "events"
            / f"{self._event_namespaces.get(event_id) or 'mod'}_events.txt"
        )
        self._dirty_event_files.add(old_path)
        del self._events[event_id]
        self._event_namespaces.pop(event_id, None)
        self._dirty.add("events")
        self._dirty_events.discard(event_id)
        return True

    def set_event_namespace(self, event_id: str, namespace: str) -> None:
        if event_id in self._events:
            namespace = require_event_namespace(namespace)
            event = self._events[event_id]
            old_path = (
                event.path
                or self.mod_root
                / "events"
                / f"{self._event_namespaces.get(event_id) or 'mod'}_events.txt"
            )
            event.path = self.mod_root / "events" / f"{namespace}_events.txt"
            event.touched = True
            self._dirty_event_files.update({old_path, event.path})
            self._event_namespaces[event_id] = namespace
            self._dirty.add("events")
            self._dirty_events.add(event_id)

    def add_event_option(self, event_id: str, option: EventOption) -> bool:
        event = self._events.get(event_id)
        if event is None:
            return False
        event.options.append(_normalize_event_option(option))
        event.options[-1].touched = True
        event.touched = True
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return True

    def update_event_option(self, event_id: str, option: int | str, **kwargs) -> bool:
        event = self._events.get(event_id)
        if event is None:
            return False
        event_option: EventOption | None
        if isinstance(option, int):
            if option < 0 or option >= len(event.options):
                return False
            event_option = event.options[option]
        else:
            event_option = next(
                (candidate for candidate in event.options if candidate.name == option), None
            )
            if event_option is None:
                return False
        for key in ("trigger", "ai_chance"):
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = normalize_block_body(kwargs[key])
        _replace_conditional_texts(kwargs, {"name": "conditional_names"})
        assert event_option is not None
        _set_fields(event_option, kwargs)
        _normalize_event_option(event_option)
        event_option.touched = True
        event.touched = True
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return True
