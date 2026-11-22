"""Event authoring."""

from __future__ import annotations

from typing import Any, Unpack

from ..fields import EnsureEventFields, EventFields, EventOptionFields
from ..events import event_namespace
from ..paths import (
    EVENT_NAMESPACE_RE,
    require_event_namespace,
    require_script_id,
)
from ..script import (
    normalize_block_body,
)
from ..types import (
    Event,
    EventOption,
    EventText,
)
from ..errors import AlreadyExistsError, NotFoundError, already_exists, not_found
from ._core import CoreLayer
from ._shared import MISSING, _D, _Missing
from ._shared import (
    _check_field_names,
    _check_fields,
    _check_model,
    _create_arguments,
    _require_items,
    _set_fields,
)


def _infer_event_namespace(event_id: str) -> str | None:
    """The namespace an event id needs: everything before its last dot.

    ``my_mod.news.1`` needs ``add_namespace = my_mod.news``.
    """

    namespace = event_namespace(event_id)
    return namespace if EVENT_NAMESPACE_RE.fullmatch(namespace) else None


def _normalize_event_option(option: EventOption) -> EventOption:
    option.trigger = normalize_block_body(option.trigger)
    option.ai_chance = normalize_block_body(option.ai_chance)
    return option


_EVENT_TEXT_LISTS = ("conditional_titles", "conditional_descriptions")


def _normalize_conditional_texts(kwargs: dict[str, Any], lists: tuple[str, ...]) -> None:
    """Turn the entries of a conditional text list into ``EventText`` objects."""

    for conditional in lists:
        if conditional in kwargs:
            kwargs[conditional] = [_event_text(entry) for entry in kwargs[conditional] or ()]


def _event_text(entry: EventText | tuple[str, str] | dict[str, str]) -> EventText:
    if isinstance(entry, EventText):
        return EventText(entry.text, normalize_block_body(entry.trigger))
    if isinstance(entry, dict):
        return EventText(str(entry["text"]), normalize_block_body(str(entry.get("trigger", ""))))
    text, trigger = entry
    return EventText(text, normalize_block_body(trigger))


class EventsLayer(CoreLayer):
    """Event authoring."""

    # ── Events ──────────────────────────────────────────────────

    def list_events(self, *, include_vanilla: bool = False) -> list[str]:
        """Return the sorted ids of the mod's events.

        Args:
            include_vanilla: Also list the events of the base mods and the
                game: every id that ``get_event()`` finds.
        """
        return self._listed("event", self._events, include_vanilla=include_vanilla)

    def get_event(
        self,
        event_id: str,
        *,
        include_vanilla: bool = True,
        default: _D | _Missing = MISSING,
    ) -> Event | _D:
        """Return the event ``event_id``: the mod's, else a base mod's, else the game's.

        An event of a base mod or the game is a copy to read: changing it
        changes nothing. To edit one, copy its file into the mod with
        ``load_inherited_content()`` (the edit's ``ReadOnlyError`` names it).

        Args:
            include_vanilla: Look at the base mods and the game too. With
                ``False`` only the mod's events count.
            default: Returned instead of raising ``NotFoundError`` when it
                does not exist. Without it, a missing id raises.

        Raises:
            NotFoundError: No event has this id.
        """
        return self._lookup(
            "event", self._events, event_id, include_vanilla=include_vanilla, default=default
        )

    def _mod_event(self, event_id: str) -> Event:
        """The mod's event ``event_id``, which an edit may change."""
        return self._mod_definition("event", self._events, event_id)

    def create_event(
        self,
        event_id: str,
        *,
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
        """Create an event; staged until ``save()``.

        ``conditional_titles``/``conditional_descriptions`` add
        ``title = { text = ... trigger = { ... } }`` entries, shown before the
        plain ``title``/``description`` fallback. The event goes into the
        file of its namespace (the id before the last dot).

        Args:
            title: The title's localisation key. Defaults to ``<event_id>.t``.
            description: The description's localisation key. Defaults to
                ``<event_id>.d``.
            event_type: ``"country_event"``, ``"news_event"`` or
                ``"state_event"`` and the like.
            trigger: Body of the ``trigger`` block; ``immediate`` and
                ``mean_time_to_happen`` likewise.
            options: Without any, one empty option ``<event_id>.a``.
            overwrite: Replace an existing event of this id.

        Returns:
            The new event.

        Raises:
            TypeError: ``options`` holds something that is not an ``EventOption``; nothing changed.
            AlreadyExistsError: The id exists and ``overwrite`` is false.
            ValueError: The id is not a valid script id.
        """
        event_id = require_script_id(event_id, label="event ID")
        if options is not None:
            options = _require_items("create_event", "options", options, (EventOption,))
        existing = self._events.get(event_id)
        if existing is not None and not overwrite:
            raise AlreadyExistsError(
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

    def ensure_event(self, event_id: str, **fields: Unpack[EnsureEventFields]) -> Event:
        """Create an event if missing, otherwise patch the existing event.

        Returns:
            The created or updated event.

        Raises:
            TypeError: A field name is unknown; nothing changed.
        """
        kwargs: dict[str, Any] = dict(fields)
        existing = self.get_event(event_id, include_vanilla=False, default=None)
        if existing is not None:
            _check_fields("ensure_event", existing, kwargs, allow_path=True)
            return self.update_event(event_id, **kwargs)
        _check_field_names(
            "ensure_event", kwargs, _create_arguments(self.create_event, skip={"event_id"})
        )
        return self.create_event(event_id, **kwargs)

    def update_event(self, event_id: str, **fields: Unpack[EventFields]) -> Event:
        """Patch fields of an event; only the changed fields are rewritten on save.

        ``title`` and ``description`` are the plain keys the game falls back
        to. Setting one leaves the conditional alternatives alone; change
        those with ``conditional_titles`` / ``conditional_descriptions``
        (an empty list removes them). ``path=`` moves the event to another file.

        Returns:
            The updated event.

        Raises:
            NotFoundError: No event has this id.
            TypeError: A field name is unknown; nothing changed.
        """
        kwargs: dict[str, Any] = dict(fields)
        event = self._mod_event(event_id)
        if "id" in kwargs:
            raise ValueError("Event IDs are immutable; create a new event instead")
        _check_fields("update_event", event, kwargs, allow_path=True)
        old_path = (
            event.path
            or self.mod_root
            / "events"
            / f"{self._event_namespaces.get(event_id) or 'mod'}_events.txt"
        )
        if "path" in kwargs:
            kwargs["path"] = self._output_path(kwargs["path"])
        for key in ("trigger", "immediate", "mean_time_to_happen"):
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = normalize_block_body(kwargs[key])
        if "options" in kwargs and isinstance(kwargs["options"], list):
            kwargs["options"] = [_normalize_event_option(option) for option in kwargs["options"]]
        _normalize_conditional_texts(kwargs, _EVENT_TEXT_LISTS)
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
        return event

    def delete_event(self, event_id: str) -> None:
        """Stage the deletion of an event and its namespace mapping.

        Raises:
            NotFoundError: No event has this id.
        """
        event = self._mod_event(event_id)
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

    def set_event_namespace(self, event_id: str, namespace: str) -> None:
        """Move an event to ``events/<namespace>_events.txt`` under that namespace.

        Raises:
            NotFoundError: No event has this id.
            ValueError: ``namespace`` is not a valid namespace.
        """
        event = self._mod_event(event_id)
        namespace = require_event_namespace(namespace)
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

    def add_event_option(self, event_id: str, option: EventOption) -> EventOption:
        """Append an option to an event.

        Returns:
            The added option.

        Raises:
            TypeError: ``option`` is not an ``EventOption``; nothing changed.
            NotFoundError: No event has this id.
            AlreadyExistsError: The event already has an option with this name.
        """
        event = self._mod_event(event_id)
        _check_model("add_event_option", option, "option")
        if option.name and any(existing.name == option.name for existing in event.options):
            raise already_exists(
                "event option",
                option.name,
                hint=f"It is in event '{event_id}'; use update_event_option() to change it.",
            )
        added = _normalize_event_option(option)
        added.touched = True
        event.options.append(added)
        event.touched = True
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return added

    def update_event_option(self, event_id: str, option: int | str, **fields: Unpack[EventOptionFields]) -> EventOption:
        """Patch one option of an event, chosen by zero-based index or by name.

        Returns:
            The updated option.

        Raises:
            NotFoundError: The event or the option does not exist.
            TypeError: A field name is unknown; nothing changed.
        """
        kwargs: dict[str, Any] = dict(fields)
        event = self._mod_event(event_id)
        event_option: EventOption | None
        if isinstance(option, int):
            if option < 0 or option >= len(event.options):
                raise NotFoundError(
                    f"Event '{event_id}' has no option {option} "
                    f"({len(event.options)} options).",
                    kind="event option",
                    id=option,
                )
            event_option = event.options[option]
        else:
            event_option = next(
                (candidate for candidate in event.options if candidate.name == option), None
            )
            if event_option is None:
                raise not_found(
                    "event option",
                    option,
                    (candidate.name for candidate in event.options if candidate.name),
                    where=f"event '{event_id}'",
                )
        _check_fields("update_event_option", event_option, kwargs)
        for key in ("trigger", "ai_chance"):
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = normalize_block_body(kwargs[key])
        _normalize_conditional_texts(kwargs, ("conditional_names",))
        assert event_option is not None
        _set_fields(event_option, kwargs)
        _normalize_event_option(event_option)
        event_option.touched = True
        event.touched = True
        self._dirty.add("events")
        self._dirty_events.add(event_id)
        return event_option
