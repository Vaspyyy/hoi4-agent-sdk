"""Localization keys count as used when script names them, read from the tree."""

import pytest

from hoi4.content_graph import _localization_keys_in_script, _script_tokens


@pytest.mark.parametrize(
    ("script", "used"),
    [
        ("custom_effect_tooltip = KEY", {"KEY"}),
        ('custom_effect_tooltip = "KEY"', {"KEY"}),
        ("KEY = { }", {"KEY"}),
        ("list = { KEY other }", {"KEY"}),
        ('log = "a text that names KEY inside"', {"KEY"}),
        ("# custom_effect_tooltip = KEY", set()),
        ("custom_effect_tooltip = OTHER # KEY", set()),
        ("custom_effect_tooltip = KEY_extra", set()),
        ("custom_effect_tooltip = KEY.extra", set()),
        ('custom_effect_tooltip = "LOC_hash#key"', {"LOC_hash#key"}),
        ("", set()),
    ],
)
def test_keys_are_found_as_keys_and_values_never_in_comments(script, used):
    keys = {"KEY", "LOC_hash#key", "MISSING"}
    assert _localization_keys_in_script(keys, _script_tokens([script])) == used


def test_one_fragment_cannot_comment_out_the_next():
    tokens = _script_tokens(['a = b # unmatched " quote', "c = KEY"])
    assert _localization_keys_in_script({"KEY"}, tokens) == {"KEY"}
