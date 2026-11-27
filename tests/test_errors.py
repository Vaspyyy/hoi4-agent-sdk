"""The SDK's error classes and the messages of a failed lookup."""

from __future__ import annotations

from pathlib import Path

import pytest

import hoi4
from hoi4 import (
    AlreadyExistsError,
    Hoi4Error,
    Mod,
    NotFoundError,
    OnAction,
    ReadOnlyError,
)
from hoi4.errors import already_exists, not_found


def test_the_error_classes_share_one_base_and_keep_the_builtin_bases() -> None:
    assert issubclass(NotFoundError, Hoi4Error)
    assert issubclass(NotFoundError, KeyError)
    assert issubclass(AlreadyExistsError, Hoi4Error)
    assert issubclass(AlreadyExistsError, ValueError)
    assert issubclass(ReadOnlyError, Hoi4Error)
    assert issubclass(ReadOnlyError, ValueError)


@pytest.mark.parametrize(
    "name",
    [
        "ParseError",
        "ExternalModificationError",
        "ImageBackendUnavailableError",
        "UnsupportedImageFormatError",
        "DDSExportUnsupportedError",
        "CommonsImageError",
        "GeminiImageError",
        "OperationCancelled",
        "MapRenderCancelled",
    ],
)
def test_the_older_error_classes_derive_from_hoi4error(name: str) -> None:
    assert issubclass(getattr(hoi4, name), Hoi4Error)


def test_generation_cancelled_derives_from_hoi4error() -> None:
    pytest.importorskip("numpy")  # the map generator needs the `map` extra
    from hoi4.mapgen.progress import GenerationCancelled

    assert issubclass(GenerationCancelled, Hoi4Error)


def test_not_found_names_close_matches_and_the_count_but_never_the_list() -> None:
    known = ["german_focus", "generic_focus", "italian_focus"] + [f"tree_{i}" for i in range(40)]
    error = not_found("focus tree", "german_focs", known)
    assert str(error) == (
        "Focus tree 'german_focs' not found. Did you mean: german_focus, generic_focus? "
        "(43 focus trees known)"
    )
    assert error.kind == "focus tree"
    assert error.id == "german_focs"
    assert error.suggestions == ("german_focus", "generic_focus")
    assert "tree_7" not in str(error)


def test_not_found_has_no_stray_quotes_unlike_a_plain_keyerror() -> None:
    error = not_found("event", "x.1", [])
    assert str(error) == "Event 'x.1' not found. (0 events known)"
    assert not str(error).startswith(("'", '"'))


def test_not_found_names_at_most_five_matches() -> None:
    known = [f"focus_{letter}" for letter in "abcdefgh"]
    error = not_found("focus", "focus_", known, plural="focuses")
    assert len(error.suggestions) == 5
    assert "(8 focuses known)" in str(error)


def test_not_found_counts_numeric_ids_without_suggesting_them() -> None:
    error = not_found("state", 99999, [1, 2, 3])
    assert str(error) == "State 99999 not found. (3 states known)"
    assert error.suggestions == ()


def test_not_found_with_place_and_hint() -> None:
    error = not_found("focus", "X", ["Y"], where="focus tree 't'", hint="Add it first.", plural="focuses")
    assert str(error) == "Focus 'X' not found in focus tree 't'. (1 focus known) Add it first."


def test_already_exists_message() -> None:
    error = already_exists("event", "x.1", hint="Pass overwrite=True to replace it.")
    assert str(error) == "Event 'x.1' already exists. Pass overwrite=True to replace it."
    assert (error.kind, error.id) == ("event", "x.1")


def test_a_failed_lookup_suggests_the_mods_close_ids(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_event("my_mod.1")
    mod.create_event("my_mod.2")
    with pytest.raises(NotFoundError) as caught:
        mod.get_event("my_mod.12")
    assert caught.value.kind == "event"
    assert caught.value.id == "my_mod.12"
    assert set(caught.value.suggestions) == {"my_mod.1", "my_mod.2"}
    assert "(2 events known)" in str(caught.value)
    # Still a KeyError for callers written against 0.6.
    with pytest.raises(KeyError):
        mod.get_event("my_mod.12")


def test_an_on_action_occurrence_out_of_range_is_not_found(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_on_action("on_startup", effect="add_political_power = 1")
    with pytest.raises(NotFoundError, match="occurrence 3 is out of range") as caught:
        mod.get_on_action("on_startup", occurrence=3)
    assert not isinstance(caught.value, IndexError)
    assert isinstance(mod.get_on_action("on_startup"), OnAction)


def test_an_unknown_state_and_country_raise_not_found(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(NotFoundError, match="State 99999 not found"):
        mod.get_state(99999)
    with pytest.raises(NotFoundError, match="Country 'ZZX' not found"):
        mod.validate_country_package("ZZX")
