from pathlib import Path

import pytest

from hoi4 import Mod


@pytest.mark.parametrize("existing", [False, True])
def test_unsupported_localization_destination_leaves_changes_untouched(
    tmp_path: Path, existing: bool
) -> None:
    german = tmp_path / "localisation/german/test_l_german.yml"
    original = '\ufeffl_german:\n FIRST:0 "Erste"\n SECOND:0 "Zweite"\n'
    if existing:
        german.parent.mkdir(parents=True)
        german.write_text(original, encoding="utf-8")
    mod = Mod(tmp_path)
    mod.set_loc("FIRST", "First")
    before = mod.preview()

    with pytest.raises(ValueError, match="localisation/english"):
        mod.set_loc("FIRST", "Neu", file_path=german)

    assert mod.get_loc("FIRST") == "First"
    assert mod.preview() == before
    result = mod.save(require_changes=True)
    assert result.written_files == [mod.default_loc_file]
    if existing:
        assert german.read_bytes() == original.encode("utf-8")
    else:
        assert not german.exists()
    assert Mod(tmp_path).get_loc("FIRST") == "First"


def test_custom_english_localization_preserves_unedited_entries(tmp_path: Path) -> None:
    relative = Path("localisation/english/nested/test_l_english.yml")
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    original = '\ufeff# translator note\nl_english:\n FIRST:0 "First"\n SECOND:0 "Second"\n'
    path.write_text(original, encoding="utf-8")
    mod = Mod(tmp_path)
    mod.set_loc("FIRST", "Updated", file_path=relative)
    result = mod.save(require_changes=True)
    assert result.written_files == [path]
    assert path.read_bytes() == original.replace('"First"', '"Updated"').encode("utf-8")
    assert Mod(tmp_path).get_loc("SECOND") == "Second"


@pytest.mark.parametrize("raise_error", [False, True])
@pytest.mark.parametrize("warm_cache", [False, True])
def test_transaction_restores_state_search_results(
    tmp_path: Path, raise_error: bool, warm_cache: bool
) -> None:
    state = tmp_path / "history/states/1-test.txt"
    state.parent.mkdir(parents=True)
    state.write_text("state = { id = 1 name = STATE_1 history = { owner = GER } }\n")
    mod = Mod(tmp_path)
    mod.set_loc("STATE_1", "Original")
    if warm_cache:
        assert mod.get_state_name_map() == {1: "Original"}
    before = mod.preview()

    try:
        with mod.transaction():
            mod.set_loc("STATE_1", "Temporary")
            assert mod.get_state_name_map() == {1: "Temporary"}
            if raise_error:
                raise RuntimeError("rollback")
    except RuntimeError as error:
        assert str(error) == "rollback"

    assert mod.get_loc("STATE_1") == "Original"
    assert mod.get_state_name_map() == {1: "Original"}
    assert mod.find_state("Original")[0]["display_name"] == "Original"
    assert mod.preview() == before


def test_nested_transaction_restores_each_state_name(tmp_path: Path) -> None:
    state = tmp_path / "history/states/1-test.txt"
    state.parent.mkdir(parents=True)
    state.write_text("state = { id = 1 name = STATE_1 }\n")
    mod = Mod(tmp_path)
    mod.set_loc("STATE_1", "Original")
    with mod.transaction():
        mod.set_loc("STATE_1", "Outer")
        assert mod.get_state_name_map() == {1: "Outer"}
        with mod.transaction():
            mod.set_loc("STATE_1", "Inner")
            assert mod.get_state_name_map() == {1: "Inner"}
        assert mod.get_state_name_map() == {1: "Outer"}
    assert mod.get_state_name_map() == {1: "Original"}
