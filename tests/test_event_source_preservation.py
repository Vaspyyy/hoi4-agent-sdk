from pathlib import Path

import pytest

from hoi4 import EventOption, Mod
from hoi4.events import load_events_file, serialize_event
from hoi4.patching import assignment_spans


SOURCE = """add_namespace = source
country_event = {
    id = source.1
    hidden = yes
    major = yes
    # Keep the conditional descriptions in their original order.
    desc = { trigger = { tag = GER } text = source.german }
    desc = { trigger = { tag = FRA } text = source.french }
    option = {
        name = source.a
        add_political_power = 1
    }
}
"""


def make_mod(tmp_path: Path, source: str = SOURCE) -> tuple[Mod, Path]:
    path = tmp_path / "events" / "source.txt"
    path.parent.mkdir()
    path.write_text(source)
    return Mod(tmp_path), path


@pytest.mark.parametrize("operation", ["event", "option", "append"])
def test_unrelated_edits_preserve_conditional_descriptions(tmp_path, operation):
    mod, path = make_mod(tmp_path)
    if operation == "event":
        mod.update_event("source.1", fire_only_once=True)
    elif operation == "option":
        mod.update_event_option("source.1", 0, effect="add_political_power = 2")
    else:
        mod.add_event_option("source.1", EventOption(name="source.b", effect="add_stability = 0.1"))
    result = mod.save(require_changes=True)
    assert path in result.written_files
    saved = path.read_text()
    for line in SOURCE.splitlines():
        if "desc =" in line or "hidden =" in line or "major =" in line or "# Keep" in line:
            assert line in saved
    event = load_events_file(path)[1][0]
    assert event.trigger == ""  # Description predicates are not event predicates.
    if operation == "append":
        assert len(event.options) == 2
    elif operation == "event":
        assert event.fire_only_once is True
    else:
        assert event.options[0].effect == "add_political_power = 2"


@pytest.mark.parametrize("replacement", ["source.replacement", ""])
def test_explicit_description_replaces_all_conditional_alternatives(tmp_path, replacement):
    mod, path = make_mod(tmp_path)
    mod.update_event("source.1", description=replacement)
    mod.save(require_changes=True)
    saved = path.read_text()
    assert "source.german" not in saved
    assert "source.french" not in saved
    assert "hidden = yes" in saved
    event = load_events_file(path)[1][0]
    assert event.description == replacement
    assert len(assignment_spans(event.raw_block, "desc")) == bool(replacement)


def test_direct_scalar_description_change_is_serialized(tmp_path):
    _, path = make_mod(
        tmp_path,
        SOURCE.replace(
            "desc = { trigger = { tag = GER } text = source.german }", "desc = source.original"
        ),
    )
    event = load_events_file(path)[1][0]
    event.description = "source.changed"
    event.touched = True
    rendered = serialize_event(event)
    assert "desc = source.changed" in rendered
    assert "source.french" not in rendered


OPTIONS_SOURCE = """add_namespace = source
country_event = {
    id = source.1
    hidden = yes
    option = {
        name = source.same
        # Owned by first.
        custom_first = { value = 01 }
        add_political_power = 1
    }
    # Between first and second.
    option = {
        name = source.same
        # Owned by second.
        custom_second = { value = 02 }
        add_political_power = 2
    }
    # Between second and third.
    option = {
        name = source.third
        # Owned by third.
        custom_third = { value = 03 }
        add_political_power = 3
    }
    # Event tail.
}
"""


@pytest.mark.parametrize(
    "order",
    [(1, 2), (0, 2), (2, 1, 0), ("new", 0, 1, 2), (0, "new", 2), ("new", 2, "new", 1), ()],
)
def test_replace_loaded_options_preserves_identity(tmp_path, order):
    mod, path = make_mod(tmp_path, OPTIONS_SOURCE)
    originals = mod.get_event("source.1").options
    replacement = [
        EventOption(name="source.new", effect="add_stability = 0.1")
        if index == "new" else originals[index]
        for index in order
    ]
    mod.update_event("source.1", options=replacement)
    before = path.read_bytes()
    preview = mod.preview()
    assert preview
    assert mod.preview() == preview
    assert path.read_bytes() == before
    result = mod.save(require_changes=True)
    print(result)
    assert result.written_files == [path]
    saved = path.read_text()
    loaded = Mod(tmp_path).get_event("source.1").options
    assert [option.name for option in loaded] == [option.name for option in replacement]
    for option, index in zip(loaded, order):
        if index == "new":
            assert option.effect == "add_stability = 0.1"
            assert "custom_" not in option.raw_block
        else:
            assert option.raw_block == originals[index].raw_block
    for index, label in enumerate(("first", "second", "third")):
        assert (f"# Owned by {label}." in saved) == (index in order)
        assert (f"custom_{label}" in saved) == (index in order)
    assert "# Between first and second." in saved
    assert "# Between second and third." in saved
    assert "# Event tail." in saved
    assert "hidden = yes" in saved

    if loaded:
        # save() reloads the same facade; edit the new position, including duplicate names.
        selected = next(position for position, index in enumerate(order) if index != "new")
        mod.update_event_option("source.1", selected, ai_chance="factor = 7")
        assert "factor = 7" in mod.preview()
        result = mod.save(require_changes=True)
        print(result)
        assert result.written_files == [path]
        edited = Mod(tmp_path).get_event("source.1").options
        assert edited[selected].ai_chance == "factor = 7"
        assert [option.effect for option in edited] == [option.effect for option in loaded]
        for position, option in enumerate(edited):
            if position != selected:
                assert option.raw_block == loaded[position].raw_block


def test_unchanged_options_are_byte_identical(tmp_path):
    from hoi4.events import serialize_events_file

    mod, path = make_mod(tmp_path, OPTIONS_SOURCE)
    namespace, events = load_events_file(path)
    assert serialize_events_file(namespace, events, OPTIONS_SOURCE) == OPTIONS_SOURCE
    mod.update_event("source.1", options=mod.get_event("source.1").options[:])
    assert mod.preview() == ""
    result = mod.save()
    print(result)
    assert result.no_changes
    assert path.read_bytes() == OPTIONS_SOURCE.encode()


def test_reordered_option_edit_before_save_uses_its_own_source(tmp_path):
    mod, path = make_mod(tmp_path, OPTIONS_SOURCE)
    options = mod.get_event("source.1").options
    mod.update_event("source.1", options=[options[1], options[0]])
    mod.update_event_option("source.1", 0, name="source.renamed", trigger="tag = GER")
    assert "source.renamed" in mod.preview()
    result = mod.save(require_changes=True)
    print(result)
    assert path in result.written_files
    options = Mod(tmp_path).get_event("source.1").options
    assert options[0].name == "source.renamed"
    assert options[0].trigger == "tag = GER"
    assert "custom_second = { value = 02 }" in options[0].raw_block
    assert "# Owned by second." in options[0].raw_block
    assert "custom_first" not in options[0].raw_block
    assert "custom_first" in options[1].raw_block


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_commented_trigger_update_preview_save_reload(tmp_path, newline):
    source = SOURCE.replace(
        "    hidden = yes",
        "    trigger = { always = yes }\n"
        "    immediate = { add_political_power = 1 }\n"
        "    mean_time_to_happen = { days = 1 }\n"
        "    hidden = yes",
    ).replace("\n", newline)
    path = tmp_path / "events" / "source.txt"
    path.parent.mkdir()
    path.write_bytes(source.encode())
    mod = Mod(tmp_path)
    mod.update_event("source.1", trigger="# explanation\nalways = no # final comment")
    preview = mod.preview()
    assert "# explanation" in preview
    assert preview == mod.preview()
    assert path.read_bytes() == source.encode()
    result = mod.save(require_changes=True)
    print(result)
    assert result.written_files == [path]
    saved = path.read_bytes().decode()
    # Event loading already normalizes newlines through Path.read_text().
    # The helper tests separately require CRLF preservation when given CRLF.
    before, after = source.replace("\r\n", "\n").split(" always = yes ")
    assert saved.startswith(before)
    assert saved.endswith(after)
    event = Mod(tmp_path).get_event("source.1")
    assert "# explanation" in event.trigger
    assert "always = no # final comment" in event.trigger
    assert event.immediate == "add_political_power = 1"
    assert event.mean_time_to_happen == "days = 1"
    assert mod.preview() == ""
