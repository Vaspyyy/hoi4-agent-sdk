from pathlib import Path

from hoi4.events import load_events_file, serialize_event, serialize_events_file, write_events_file
from hoi4.types import Event, EventOption

FIXTURES = Path(__file__).parent / "fixtures"


class TestLoadEventsFile:
    def test_reads_namespace(self):
        ns, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        assert ns == "mymod.1"

    def test_reads_all_events(self):
        _, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        assert len(events) == 2

    def test_reads_event_id(self):
        _, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        assert events[0].id == "mymod.1.1"
        assert events[1].id == "mymod.1.2"

    def test_reads_event_type(self):
        _, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        assert events[0].event_type == "country_event"

    def test_reads_title_and_desc(self):
        _, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        assert events[0].title == "mymod.1.1.t"
        assert events[0].description == "mymod.1.1.d"

    def test_reads_picture(self):
        _, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        assert events[0].picture == "GFX_report_event_generic"

    def test_reads_triggered_only(self):
        _, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        assert events[0].is_triggered_only is True
        assert events[1].is_triggered_only is False

    def test_reads_trigger(self):
        _, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        assert "tag = GER" in events[0].trigger

    def test_reads_mean_time(self):
        _, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        assert "days" in events[0].mean_time_to_happen

    def test_reads_options(self):
        _, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        assert len(events[0].options) == 2
        assert events[0].options[0].name == "mymod.1.1.a"

    def test_reads_option_trigger(self):
        _, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        assert "has_war" in events[0].options[1].trigger

    def test_reads_option_effect(self):
        _, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        assert "add_political_power" in events[0].options[0].effect

    def test_ignores_commented_event_template_and_nested_event_effect(self, tmp_path):
        p = tmp_path / "norway_like.txt"
        p.write_text(
            """
add_namespace = test_ns

#country_event = {
#   id = commented.1
#   option = {
#}

country_event = {
    id = test_ns.1
    title = test_ns.1.t
    desc = test_ns.1.d
    picture = GFX_report_event_generic
    fire_only_once = yes
    immediate = { add_political_power = 1 }
    option = {
        name = test_ns.1.a
        ai_chance = { base = 10 }
        country_event = { id = test_ns.2 days = 1 }
    }
}

country_event = {
    id = test_ns.2
    title = test_ns.2.t
    desc = test_ns.2.d
    picture = GFX_report_event_generic
    option = { name = test_ns.2.a }
}
""",
            encoding="utf-8",
        )
        ns, events = load_events_file(p)
        assert ns == "test_ns"
        assert [event.id for event in events] == ["test_ns.1", "test_ns.2"]
        assert events[0].fire_only_once is True
        assert "add_political_power" in events[0].immediate
        assert events[0].options[0].ai_chance == "base = 10"


class TestSerializeEvent:
    def test_roundtrip_id(self):
        _, events = load_events_file(FIXTURES / "events" / "mymod_events.txt")
        text = serialize_event(events[0])
        assert "id = mymod.1.1" in text
        assert "country_event" in text

    def test_serializes_new_event(self):
        event = Event(
            id="test.1",
            title="test.1.t",
            description="test.1.d",
            options=[
                EventOption(name="test.1.a", effect="add_stability = 0.05"),
            ],
        )
        text = serialize_event(event)
        assert "id = test.1" in text
        assert "name = test.1.a" in text
        assert "add_stability" in text

    def test_preserves_final_scoped_effect_brace_after_reload(self, tmp_path):
        path = tmp_path / "events.txt"
        path.write_text(
            """
add_namespace = sic

country_event = {
    id = sic.1
    title = sic.1.t
    desc = sic.1.d
    option = {
        name = sic.1.a
        SCL = {
            transfer_state = 115
        }
    }
}
""",
            encoding="utf-8",
        )
        _, events = load_events_file(path)
        text = serialize_event(Event(
            id=events[0].id,
            title=events[0].title,
            description=events[0].description,
            options=events[0].options,
        ))
        assert "SCL = {" in text
        assert "transfer_state = 115" in text
        assert "\n\t\t}" in text


class TestSerializeEventsFile:
    def test_includes_namespace(self):
        event = Event(id="test.1", options=[EventOption(name="test.1.a")])
        text = serialize_events_file("mymod", [event])
        assert "add_namespace = mymod" in text

    def test_multiple_events(self):
        events = [
            Event(id="a.1", options=[EventOption(name="a.1.a")]),
            Event(id="a.2", options=[EventOption(name="a.2.a")]),
        ]
        text = serialize_events_file("a", events)
        assert "id = a.1" in text
        assert "id = a.2" in text


class TestWriteEventsFile:
    def test_writes_and_reads_back(self, tmp_path):
        events = [
            Event(
                id="t.1", title="t.1.t", description="t.1.d",
                event_type="country_event",
                options=[EventOption(name="t.1.a", effect="add_pp = 50")],
            ),
        ]
        path = write_events_file(tmp_path / "test_events.txt", "t", events)
        assert path.exists()

        ns, loaded = load_events_file(path)
        assert ns == "t"
        assert loaded[0].id == "t.1"
        assert loaded[0].title == "t.1.t"
