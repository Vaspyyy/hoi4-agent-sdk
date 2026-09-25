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
