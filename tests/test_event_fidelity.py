"""Events are read from the syntax tree and written back span by span."""

from pathlib import Path

import pytest

from hoi4 import EventOption, EventText, Mod
from hoi4.events import load_events_file, scan_event_ids_file, serialize_events_file
from hoi4.validation import validate_event

PROBE = """add_namespace = probe
country_event = {
\tid = probe.1
\ttitle = {
\t\ttext = probe.1.t_a
\t\ttrigger = { has_war = yes }
\t}
\ttitle = { text = probe.1.t_b }
\tdesc = probe.1.d
\tpicture = GFX_report_event_generic
\toption = { name = probe.1.a }
}
"""

CONDITIONAL = """add_namespace = cond
# add_namespace = commented_out
country_event = {
\t# id = cond.fake
\timmediate = {
\t\tcountry_event = { id = cond.nested days = 2 }
\t}
\tid = cond.1
\ttitle = cond.1.t # the fallback comes first here
\tdesc = {
\t\ttext = cond.1.d_ger # Germany
\t\ttrigger = {
\t\t\ttag = GER
\t\t}
\t}
\tdesc = {
\t\ttrigger = { tag = FRA }
\t\ttext = cond.1.d_fra
\t}
\tdesc = { text = cond.1.d }
\tunknown_key = { keep = me }
\tis_triggered_only = yes

\toption = {
\t\tname = {
\t\t\ttext = cond.1.a_war
\t\t\ttrigger = { has_war = yes }
\t\t}
\t\tname = cond.1.a
\t\t# keep this comment
\t\tadd_political_power = 10
\t}
\toption = {}
}
"""


def make_mod(tmp_path: Path, source: str, name: str = "probe.txt") -> tuple[Mod, Path]:
    path = tmp_path / "events" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return Mod(tmp_path), path


def test_conditional_titles_are_read_not_braces(tmp_path):
    mod, _ = make_mod(tmp_path, PROBE)
    event = mod.get_event("probe.1")
    assert event.title == "probe.1.t_b"
    assert event.conditional_titles == [EventText("probe.1.t_a", "has_war = yes")]
    assert event.description == "probe.1.d"
    assert event.options[0].name == "probe.1.a"


def test_picture_edit_changes_only_the_picture_value(tmp_path):
    mod, path = make_mod(tmp_path, PROBE)
    mod.update_event("probe.1", picture="GFX_report_event_other")
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == PROBE.replace(
        "GFX_report_event_generic", "GFX_report_event_other"
    )


def test_tree_reading_ignores_comments_and_nested_keys(tmp_path):
    mod, path = make_mod(tmp_path, CONDITIONAL)
    namespace, events = load_events_file(path)
    assert namespace == "cond"
    assert [event.id for event in events] == ["cond.1"]
    assert scan_event_ids_file(path) == {"cond.1"}
    event = mod.get_event("cond.1")
    assert event.title == "cond.1.t"
    assert event.description == "cond.1.d"
    assert event.conditional_descriptions == [
        EventText("cond.1.d_ger", "tag = GER"),
        EventText("cond.1.d_fra", "tag = FRA"),
    ]
    assert event.is_triggered_only is True
    first, empty = event.options
    assert first.name == "cond.1.a"
    assert first.conditional_names == [EventText("cond.1.a_war", "has_war = yes")]
    assert "add_political_power = 10" in first.effect
    assert "text" not in first.effect
    assert empty.name == "" and empty.effect == ""


@pytest.mark.parametrize(
    "edit",
    [
        lambda mod: mod.update_event("cond.1", picture="GFX_report_event_generic"),
        lambda mod: mod.update_event("cond.1", is_triggered_only=True),
        lambda mod: mod.update_event_option("cond.1", 1, effect=""),
        lambda mod: mod.update_event(
            "cond.1",
            title="cond.1.t",
            conditional_titles=[],
            description="cond.1.d",
            conditional_descriptions=list(mod.get_event("cond.1").conditional_descriptions),
        ),
        lambda mod: mod.update_event_option(
            "cond.1", 0,
            name="cond.1.a",
            conditional_names=[EventText("cond.1.a_war", "has_war = yes")],
            effect="add_political_power = 10",
        ),
    ],
)
def test_no_op_edits_are_byte_identical(tmp_path, edit):
    mod, path = make_mod(tmp_path, CONDITIONAL)
    edit(mod)
    mod.save()
    assert path.read_text(encoding="utf-8") == CONDITIONAL


def test_editing_one_conditional_text_touches_only_that_text(tmp_path):
    mod, path = make_mod(tmp_path, CONDITIONAL)
    entries = list(mod.get_event("cond.1").conditional_descriptions)
    entries[1] = EventText("cond.1.d_fra_new", entries[1].trigger)
    mod.update_event("cond.1", description="cond.1.d", conditional_descriptions=entries)
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == CONDITIONAL.replace(
        "text = cond.1.d_fra", "text = cond.1.d_fra_new"
    )


def test_editing_a_conditional_trigger_touches_only_that_trigger(tmp_path):
    mod, path = make_mod(tmp_path, CONDITIONAL)
    entries = list(mod.get_event("cond.1").conditional_descriptions)
    entries[1] = EventText(entries[1].text, "tag = ITA")
    mod.update_event("cond.1", description="cond.1.d", conditional_descriptions=entries)
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == CONDITIONAL.replace(
        "trigger = { tag = FRA }", "trigger = { tag = ITA }"
    )


def test_adding_and_removing_conditional_titles(tmp_path):
    mod, path = make_mod(tmp_path, PROBE)
    mod.update_event(
        "probe.1",
        title="probe.1.t_b",
        conditional_titles=[
            EventText("probe.1.t_a", "has_war = yes"),
            EventText("probe.1.t_c", "tag = GER"),
        ],
    )
    mod.save(require_changes=True)
    text = path.read_text(encoding="utf-8")
    assert text == PROBE.replace(
        "\ttitle = { text = probe.1.t_b }",
        "\ttitle = { text = probe.1.t_c trigger = { tag = GER } }\n"
        "\ttitle = { text = probe.1.t_b }",
    )
    mod.reload()
    assert [entry.text for entry in mod.get_event("probe.1").conditional_titles] == [
        "probe.1.t_a",
        "probe.1.t_c",
    ]

    mod.update_event(
        "probe.1", title="probe.1.t_b", conditional_titles=[EventText("probe.1.t_a", "has_war = yes")]
    )
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == PROBE


def test_plain_title_update_keeps_conditional_alternatives(tmp_path):
    mod, path = make_mod(tmp_path, PROBE)
    mod.update_event("probe.1", title="probe.1.t_new")
    assert mod.get_event("probe.1").conditional_titles == [
        EventText("probe.1.t_a", "has_war = yes")
    ]
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == PROBE.replace(
        "title = { text = probe.1.t_b }", "title = { text = probe.1.t_new }"
    )


def test_plain_option_name_update_keeps_conditional_names(tmp_path):
    mod, path = make_mod(tmp_path, CONDITIONAL)
    mod.update_event_option("cond.1", 0, name="cond.1.a2")
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == CONDITIONAL.replace(
        "\t\tname = cond.1.a\n", "\t\tname = cond.1.a2\n"
    )


def test_an_empty_conditional_list_removes_the_alternatives(tmp_path):
    mod, path = make_mod(tmp_path, PROBE)
    mod.update_event("probe.1", conditional_titles=[])
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == PROBE.replace(
        "\ttitle = {\n\t\ttext = probe.1.t_a\n\t\ttrigger = { has_war = yes }\n\t}\n", ""
    )


def test_option_name_block_survives_effect_edit(tmp_path):
    mod, path = make_mod(tmp_path, CONDITIONAL)
    mod.update_event_option("cond.1", 0, effect="add_stability = 0.1")
    mod.save(require_changes=True)
    text = path.read_text(encoding="utf-8")
    assert text == CONDITIONAL.replace(
        "\t\t# keep this comment\n\t\tadd_political_power = 10\n",
        "\t\t# keep this comment\n\t\tadd_stability = 0.1\n",
    )


def test_new_picture_goes_after_the_description(tmp_path):
    source = PROBE.replace("\tpicture = GFX_report_event_generic\n", "")
    mod, path = make_mod(tmp_path, source)
    mod.update_event("probe.1", picture="GFX_report_event_other")
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == PROBE.replace(
        "GFX_report_event_generic", "GFX_report_event_other"
    )


def test_create_event_with_conditional_texts_round_trips(tmp_path):
    (tmp_path / "events").mkdir()
    mod = Mod(tmp_path)
    mod.create_event(
        "made.1",
        title="made.1.t",
        conditional_titles=[EventText("made.1.t_war", "has_war = yes")],
        conditional_descriptions=[EventText("made.1.d_ger", "tag = GER\nhas_war = no")],
        options=[
            EventOption(
                name="made.1.a",
                conditional_names=[EventText("made.1.a_war", "has_war = yes")],
                effect="add_political_power = 5",
            )
        ],
    )
    mod.save(require_changes=True)
    mod.reload()
    event = mod.get_event("made.1")
    assert event.title == "made.1.t"
    assert event.conditional_titles == [EventText("made.1.t_war", "has_war = yes")]
    assert event.description == "made.1.d"
    assert event.conditional_descriptions == [EventText("made.1.d_ger", "tag = GER\nhas_war = no")]
    assert event.options[0].conditional_names == [EventText("made.1.a_war", "has_war = yes")]
    assert event.options[0].effect == "add_political_power = 5"


def test_unchanged_file_round_trips_through_serializer(tmp_path):
    path = tmp_path / "events.txt"
    path.write_text(CONDITIONAL, encoding="utf-8")
    namespace, events = load_events_file(path)
    for event in events:
        event.touched = True
        for option in event.options:
            option.touched = True
    assert serialize_events_file(namespace, events, CONDITIONAL) == CONDITIONAL


def test_validation_accepts_conditional_only_titles_and_descriptions(tmp_path):
    source = PROBE.replace("\ttitle = { text = probe.1.t_b }\n", "").replace(
        "\tdesc = probe.1.d\n", "\tdesc = { text = probe.1.d trigger = { has_war = no } }\n"
    )
    mod, _ = make_mod(tmp_path, source)
    event = mod.get_event("probe.1")
    assert event.title == "" and event.description == ""
    messages = [error.message for error in validate_event(event)]
    assert not any("has no title" in message for message in messages)
    assert not any("has no description" in message for message in messages)

    event.conditional_titles.append(EventText(""))
    assert any(
        error.code == "event_conditional_text_missing" for error in validate_event(event)
    )


COMMENTED_OPTION = """add_namespace = ev
country_event = {
\tid = ev.1
\ttitle = ev.1.t
\ttrigger = {
\t\thas_war = yes # why
\t}
\toption = {
\t\tname = ev.1.a # the good one
\t\t# lead comment
\t\tai_chance = { factor = 1 }
\t\tadd_stability = 0.1 # stab
\t}
}
"""


def test_editing_an_option_effect_never_copies_its_comments(tmp_path):
    mod, path = make_mod(tmp_path, COMMENTED_OPTION)
    expected = COMMENTED_OPTION
    for power in (1, 2, 3):
        option = mod.get_event("ev.1").options[0]
        mod.update_event_option(
            "ev.1", 0, effect=option.effect + f"\nadd_political_power = {power}"
        )
        mod.save(require_changes=True)
        expected = expected.replace("\t}\n}\n", f"\t\tadd_political_power = {power}\n\t}}\n}}\n")
        assert path.read_text(encoding="utf-8") == expected
        mod.reload()
        assert mod.preview() == ""


def test_a_new_option_effect_keeps_the_comment_of_a_changed_statement(tmp_path):
    mod, path = make_mod(tmp_path, COMMENTED_OPTION)
    mod.update_event_option("ev.1", 0, effect="add_stability = 0.2")
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == COMMENTED_OPTION.replace(
        "add_stability = 0.1", "add_stability = 0.2"
    )


def test_the_comment_on_the_name_line_is_not_part_of_the_effect(tmp_path):
    mod, _ = make_mod(tmp_path, COMMENTED_OPTION)
    assert mod.get_event("ev.1").options[0].effect == "# lead comment\nadd_stability = 0.1 # stab"


def test_a_comment_only_trigger_edit_is_written(tmp_path):
    mod, path = make_mod(tmp_path, COMMENTED_OPTION)
    mod.update_event("ev.1", trigger="has_war = yes # because")
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == COMMENTED_OPTION.replace("# why", "# because")


def test_a_picture_that_needs_quotes_gets_them(tmp_path):
    mod, path = make_mod(tmp_path, PROBE)
    mod.update_event("probe.1", picture="GFX a#b")
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == PROBE.replace(
        "picture = GFX_report_event_generic", 'picture = "GFX a#b"'
    )
    mod.reload()
    assert mod.get_event("probe.1").picture == "GFX a#b"


ONE_LINE_OPTIONS = """add_namespace = t
country_event = {
\tid = t.1
\toption = { name = t.1.a add_political_power = 10 }
\toption = { name = t.1.b }
\toption = { add_political_power = 1 name = t.1.c }
}
"""


@pytest.mark.parametrize(
    "index, effect, old, new",
    [
        (
            0, "add_political_power = 10 # note",
            "\toption = { name = t.1.a add_political_power = 10 }\n",
            "\toption = {\n\t\tname = t.1.a\n\t\tadd_political_power = 10 # note\n\t}\n",
        ),
        (
            1, "add_stability = 1 # s",
            "\toption = { name = t.1.b }\n",
            "\toption = {\n\t\tname = t.1.b\n\t\tadd_stability = 1 # s\n\t}\n",
        ),
        (
            2, "add_political_power = 2 # c",
            "\toption = { add_political_power = 1 name = t.1.c }\n",
            "\toption = {\n\t\tadd_political_power = 2 # c\n\t\tname = t.1.c\n\t}\n",
        ),
        (
            1, "add_stability = 1",
            "\toption = { name = t.1.b }\n",
            "\toption = { name = t.1.b add_stability = 1 }\n",
        ),
    ],
)
def test_a_comment_in_a_one_line_option_never_reaches_the_closing_brace(
    tmp_path, index, effect, old, new
):
    mod, path = make_mod(tmp_path, ONE_LINE_OPTIONS)
    options = list(mod.get_event("t.1").options)
    options[index].effect = effect
    mod.update_event("t.1", options=options)
    mod.save(require_changes=True)
    assert path.read_text(encoding="utf-8") == ONE_LINE_OPTIONS.replace(old, new)
    mod.reload()
    assert mod.get_event("t.1").options[index].effect == effect


def test_an_option_effect_that_would_end_the_option_early_is_refused(tmp_path):
    """Balanced braces are not enough: the effect has to be well formed on its own."""

    mod, path = make_mod(tmp_path, ONE_LINE_OPTIONS)
    options = list(mod.get_event("t.1").options)
    options[0].effect = "add_political_power = 10 }\noption = {"
    mod.update_event("t.1", options=options)
    with pytest.raises(ValueError, match="effect of an event option"):
        mod.save()
    assert path.read_text(encoding="utf-8") == ONE_LINE_OPTIONS


@pytest.mark.parametrize(
    "create, where",
    [
        (
            lambda mod: mod.create_event(
                "t.2", title="t.2.t", immediate="add_stability = 0.1 }\ncountry_event = {"
            ),
            "in the immediate of event 't.2'",
        ),
        (
            lambda mod: mod.create_event(
                "t.2", title="t.2.t",
                options=[EventOption(name="t.2.a", effect="add_stability = 0.1 }\noption = {")],
            ),
            "in the effect of an event option",
        ),
        (
            lambda mod: mod.add_event_option(
                "t.1", EventOption(name="t.1.d", trigger="has_war = yes }\noption = {")
            ),
            "in the trigger of an event option",
        ),
    ],
)
def test_new_events_and_options_with_a_stray_brace_are_refused(tmp_path, create, where):
    mod, path = make_mod(tmp_path, ONE_LINE_OPTIONS)
    create(mod)
    with pytest.raises(ValueError, match=where):
        mod.save()
    assert path.read_text(encoding="utf-8") == ONE_LINE_OPTIONS


def test_new_statement_goes_below_a_shared_line_that_ends_in_a_comment(tmp_path: Path) -> None:
    source = (
        "add_namespace = t\ncountry_event = {\n\tid = t.1 desc = t.1.d # note\n"
        "\toption = { name = t.1.a }\n}\n"
    )
    path = tmp_path / "events/t.txt"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    mod = Mod(tmp_path)
    mod.update_event("t.1", picture="GFX_x")
    mod.save()
    assert path.read_text(encoding="utf-8") == source.replace(
        "# note\n", "# note\n\tpicture = GFX_x\n"
    )


def test_new_statement_goes_below_the_opening_line_that_ends_in_a_comment(tmp_path: Path) -> None:
    source = (
        "add_namespace = e\ncountry_event = { id = e.1 desc = e.1.d # desc note\n"
        "\toption = { name = e.1.a }\n}\n"
    )
    path = tmp_path / "events/e.txt"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    mod = Mod(tmp_path)
    mod.update_event("e.1", picture="GFX_p")
    mod.save()
    assert path.read_text(encoding="utf-8") == source.replace(
        "# desc note\n", "# desc note\n\tpicture = GFX_p\n"
    )
