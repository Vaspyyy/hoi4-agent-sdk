"""Country tags are creation metadata, not a selector editing API."""

from pathlib import Path

import pytest

from hoi4 import Mod


COMPLEX_SELECTOR = """country = {
        factor = 0 # Keep the weighted selector
        modifier = {
            add = 10
            original_tag = GER
            OR = { tag = GER tag = AUS }
            custom_selector_condition = yes
        }
        modifier = { add = 5 tag = SOV }
    }"""


@pytest.mark.parametrize(
    "source", ["loaded", "loaded_edited", "complex", "complex_edited", "created"]
)
@pytest.mark.parametrize("country_tag", ["GER", "SOV"])
@pytest.mark.parametrize("mixed_fields", [False, True])
def test_rejected_country_tag_preserves_pending_work(
    tmp_path: Path, source: str, country_tag: str, mixed_fields: bool,
) -> None:
    path = tmp_path / "common/national_focus/GER_focus.txt"
    original = (Path(__file__).parent / "fixtures/GER_focus.txt").read_text()
    if source.startswith("complex"):
        start = original.index("country = {")
        end = original.index("\n\n\tfocus =", start)
        original = original[:start] + COMPLEX_SELECTOR + original[end:]
    if source != "created":
        path.parent.mkdir(parents=True)
        path.write_text(original, encoding="utf-8")
    mod = Mod(tmp_path)
    if source == "created":
        mod.create_focus_tree("german_focus", "GER")
    elif source.endswith("_edited"):
        assert mod.update_focus_tree("german_focus", continuous_focus_position="x = 0 y = 1000")
    tree = mod.get_focus_tree("german_focus")
    original_position = tree.continuous_focus_position
    mod.set_loc("PENDING_WORK", "Keep staged localization")
    before = mod._snapshot()
    preview = mod.preview()
    # Supported fields precede country_tag to catch partial mixed-call mutation.
    fields = {
        "continuous_focus_position": "x = 99 y = 999",
        "path": "common/national_focus/rejected.txt",
    } if mixed_fields else {}
    fields["country_tag"] = country_tag

    with pytest.raises(TypeError, match="Remove country_tag.*selector reassignment.*not supported"):
        mod.update_focus_tree("german_focus", **fields)

    assert mod._snapshot() == before
    assert mod.preview() == preview
    assert tree.country_tag == "GER"
    assert tree.continuous_focus_position == original_position
    result = mod.save(require_changes=True)
    assert result.written_files
    assert not (tmp_path / "common/national_focus/rejected.txt").exists()
    if source in {"loaded", "complex"}:
        assert path.read_bytes() == original.encode("utf-8")
        assert path not in result.written_files
    if source.startswith("complex"):
        assert COMPLEX_SELECTOR in path.read_text(encoding="utf-8")
    reloaded = Mod(tmp_path)
    assert reloaded.get_focus_tree("german_focus").country_tag == "GER"
    assert reloaded.get_focus_tree("german_focus").continuous_focus_position == original_position
    assert reloaded.get_loc("PENDING_WORK") == "Keep staged localization"


def test_rejected_country_tag_keeps_clean_tree_clean(tmp_path: Path) -> None:
    path = tmp_path / "common/national_focus/GER_focus.txt"
    path.parent.mkdir(parents=True)
    original = (Path(__file__).parent / "fixtures/GER_focus.txt").read_bytes()
    path.write_bytes(original)
    mod = Mod(tmp_path)
    before = mod._snapshot()

    with pytest.raises(TypeError, match="country_tag"):
        mod.update_focus_tree("german_focus", country_tag="SOV")

    assert mod._snapshot() == before
    assert mod.preview() == ""
    with pytest.warns(RuntimeWarning, match="No changes written"):
        assert not mod.save().written_files
    assert path.read_bytes() == original
    assert Mod(tmp_path).get_focus_tree("german_focus").country_tag == "GER"


def test_missing_focus_tree_update_still_returns_false(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    assert mod.update_focus_tree("missing", country_tag="GER") is False
