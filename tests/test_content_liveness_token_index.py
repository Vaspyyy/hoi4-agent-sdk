import re

import pytest

from hoi4.content_graph import _localization_keys_in_script


@pytest.mark.parametrize(
    "script",
    [
        'title = "KEY"\nKEY_extra extra_KEY KEY2 2KEY KEY.extra KEY:extra KEY-extra',
        "KEY.extra KEY:extra KEY-extra _KEY_ .KEY. :KEY: -KEY-",
        'KEY /KEY/ [KEY] (KEY) KEY@OTHER "A B" A/B A+B "café"',
        "prefix.KEY.suffix prefix-KEY:suffix KEY.EXTRA KEY.extra",
        'KEY🙂 "🙂" ÄKEY "KEYÄ" KÉY A@B xA@B A@Bx',
        "",
    ],
)
def test_token_index_matches_previous_regex_boundaries_exactly(script):
    keys = {
        "KEY",
        "KEY_extra",
        "extra_KEY",
        "KEY2",
        "2KEY",
        "KEY.extra",
        "KEY:extra",
        "KEY-extra",
        "_KEY_",
        ".KEY.",
        ":KEY:",
        "-KEY-",
        "KEY.EXTRA",
        "A B",
        "A/B",
        "A+B",
        "café",
        "🙂",
        "ÄKEY",
        "KEYÄ",
        "KÉY",
        "A@B",
        "",
        "MISSING",
    }
    expected = {
        key
        for key in keys
        if re.search(rf"(?<![A-Za-z0-9_.:-]){re.escape(key)}(?![A-Za-z0-9_.:-])", script)
    }
    assert _localization_keys_in_script(keys, script) == expected


def test_standard_keys_do_not_trigger_per_key_full_script_search(monkeypatch):
    import hoi4.content_graph as graph

    def fail_search(*args, **kwargs):
        raise AssertionError("Standard keys must use the shared token index")

    monkeypatch.setattr(graph.re, "search", fail_search)
    keys = {f"ABC_name_{index}" for index in range(10000)}
    assert graph._localization_keys_in_script(
        keys, "ABC_name_1 ABC_name_3.extra ABC_name_9999"
    ) == {"ABC_name_1", "ABC_name_9999"}
