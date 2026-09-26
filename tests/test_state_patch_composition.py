"""Pending state edits share one source-preserving representation."""

import pytest

from hoi4 import Mod
from hoi4.diff import unified_diff


SOURCE = """# retained file comment
state = {
 id = 1
 name = STATE_1
 manpower = 100 # retained manpower comment
 state_category = town
 provinces = { 1 2 }
 custom_state = { keep = yes }
 history = {
  owner = GER # retained owner comment
  add_core_of = GER
  # retained history comment
  victory_points = { 1 5 } # exact victory-point formatting
  buildings = { infrastructure = 2 }
  1939.1.1 = { owner = FRA add_core_of = FRA }
 }
}
"""


@pytest.fixture
def state_mod(tmp_path):
    path = tmp_path / "history" / "states" / "1-Test.txt"
    path.parent.mkdir(parents=True)
    path.write_text(SOURCE, encoding="utf-8")
    return Mod(tmp_path), path


def assert_saved(mod, path, owner, cores, manpower="100"):
    state = mod.get_state(1)
    assert (state.owner, state.cores, state.manpower) == (owner, cores, manpower)
    preview = mod.preview()
    assert mod.preview() == preview
    assert path.read_text(encoding="utf-8") == SOURCE
    result = mod.save(require_changes=True)
    assert result.written_files == [path]
    saved = path.read_text(encoding="utf-8")
    assert preview == unified_diff(SOURCE, saved, str(path.relative_to(mod.mod_root)))
    assert mod.preview() == ""
    for fragment in (
        "# retained file comment",
        "# retained manpower comment",
        "# retained owner comment",
        "  # retained history comment\n",
        "  victory_points = { 1 5 } # exact victory-point formatting\n",
        "  buildings = { infrastructure = 2 }\n",
        "  1939.1.1 = { owner = FRA add_core_of = FRA }\n",
        " custom_state = { keep = yes }\n",
    ):
        assert fragment in saved
    reloaded = Mod(mod.mod_root).get_state(1)
    assert (reloaded.owner, reloaded.cores, reloaded.manpower) == (owner, cores, manpower)
    return saved


def test_repeated_owner_and_core_patches(state_mod):
    mod, path = state_mod
    mod.patch_state_history(1, owner="ITA")
    mod.patch_state_history(1, owner="ENG")
    mod.patch_state_history(1, add_cores=["ITA"])
    mod.patch_state_history(1, add_cores=["ENG"])
    mod.patch_state_history(1, add_cores=["ITA"])
    assert_saved(mod, path, "ENG", ["GER", "ITA", "ENG"])


@pytest.mark.parametrize("ordinary", [False, True])
@pytest.mark.parametrize("remove_first", [False, True])
def test_core_add_remove_order(state_mod, ordinary, remove_first):
    mod, path = state_mod
    tag = "GER" if remove_first else "ITA"
    if remove_first:
        mod.patch_state_history(1, remove_cores=[tag])
        if ordinary:
            mod.add_state_core(1, tag)
        else:
            mod.patch_state_history(1, add_cores=[tag])
    else:
        mod.patch_state_history(1, add_cores=[tag])
        if ordinary:
            mod.remove_state_core(1, tag)
        else:
            mod.patch_state_history(1, remove_cores=[tag])
    # Leave a real change even when the core operations cancel out.
    mod.set_state_properties(1, manpower="200")
    assert_saved(mod, path, "GER", ["GER"], "200")


@pytest.mark.parametrize("patch_first", [False, True])
@pytest.mark.parametrize("setter", ["properties", "owner", "add_core", "remove_core"])
def test_history_patch_and_ordinary_setter(state_mod, patch_first, setter):
    mod, path = state_mod

    def patch():
        mod.patch_state_history(1, owner="ITA", add_cores=["ITA"])

    def edit():
        if setter == "properties":
            mod.set_state_properties(1, manpower="200", owner="ENG", cores=["ENG"])
        elif setter == "owner":
            mod.set_state_owner(1, "ENG")
        elif setter == "add_core":
            mod.add_state_core(1, "ENG")
        else:
            mod.remove_state_core(1, "GER")

    for operation in ((patch, edit) if patch_first else (edit, patch)):
        operation()
    owner = "ENG" if patch_first and setter in {"owner", "properties"} else "ITA"
    cores = ["ITA"] if setter == "remove_core" else (
        ["GER", "ITA", "ENG"] if patch_first else ["GER", "ENG", "ITA"]
    )
    assert_saved(mod, path, owner, cores, "200" if setter == "properties" else "100")


def test_pending_patch_transaction_discard_and_second_save(state_mod):
    mod, path = state_mod
    mod.patch_state_history(1, owner="ITA")
    before = mod.preview()
    with mod.transaction():
        mod.patch_state_history(1, add_cores=["ENG"])
        mod.set_state_properties(1, manpower="200")
    assert mod.preview() == before
    assert_saved(mod, path, "ITA", ["GER"])
    mod.patch_state_history(1, add_cores=["ITA"])
    mod.set_state_properties(1, manpower="300")
    mod.save(require_changes=True)
    reloaded = Mod(mod.mod_root).get_state(1)
    assert (reloaded.owner, reloaded.cores, reloaded.manpower) == ("ITA", ["GER", "ITA"], "300")
    mod.patch_state_history(1, owner="ENG")
    mod.discard()
    assert mod.get_state(1).owner == "ITA"
    assert mod.preview() == ""


def test_mixed_edits_change_only_requested_source_tokens(state_mod):
    mod, path = state_mod
    mod.patch_state_history(1, owner="ITA")
    mod.set_state_properties(1, manpower="200")
    mod.patch_state_history(1, owner="ENG")
    saved = assert_saved(mod, path, "ENG", ["GER"], "200")
    assert saved == SOURCE.replace("owner = GER", "owner = ENG").replace(
        "manpower = 100", "manpower = 200"
    )


def test_composed_edits_materialize_inherited_state_only(tmp_path):
    base = tmp_path / "base"
    source = base / "history" / "states" / "1-Test.txt"
    source.parent.mkdir(parents=True)
    source.write_text(SOURCE, encoding="utf-8")
    target = tmp_path / "mod"
    target.mkdir()
    mod = Mod(target, base_mod_paths=[base])
    mod.patch_state_history(1, owner="ITA")
    mod.set_state_properties(1, manpower="200")
    mod.patch_state_history(1, add_cores=["ITA"])
    output = target / "history" / "states" / source.name
    preview = mod.preview()
    assert not output.exists()
    result = mod.save(require_changes=True)
    assert result.written_files == [output]
    assert source.read_text(encoding="utf-8") == SOURCE
    assert preview == unified_diff("", output.read_text(encoding="utf-8"), str(output.relative_to(target)))
    state = Mod(target).get_state(1)
    assert (state.owner, state.cores, state.manpower) == ("ITA", ["GER", "ITA"], "200")


def test_patch_removal_retains_case_insensitive_source_matching(state_mod):
    mod, path = state_mod
    path.write_text(SOURCE.replace("add_core_of = GER", "add_core_of = ger"), encoding="utf-8")
    mod = Mod(mod.mod_root)
    mod.patch_state_history(1, remove_cores=["ger"])
    assert mod.get_state(1).cores == []
    result = mod.save(require_changes=True)
    assert result.written_files == [path]
    assert Mod(mod.mod_root).get_state(1).cores == []
