from pathlib import Path

import pytest

from hoi4 import Mod


IDEOLOGIES_PATH = Path("common/ideologies/00_mod_ideologies.txt")
SIBLING = """    # Keep this sibling exactly.
    sibling = { color = { 4 5 6 } future_rule = { keep = yes } }
"""
INHERITED = """# File header
ideologies = {
    first = { color = { 1 2 3 } future_setting = yes } # First comment
""" + SIBLING + "}\n# File footer\n"


def put_ideologies(root, text, relative=IDEOLOGIES_PATH):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("control", ["missing", "populated", "empty", "suppressed", "base_suppressed"])
def test_inherited_ideology_preview_save_reload(tmp_path, monkeypatch, operation, control):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    lower, base, top = (tmp_path / name for name in ("lower", "base", "mod"))
    lower_file = put_ideologies(lower, "ideologies = { hidden = { color = { 0 0 0 } } }\n")
    base_file = put_ideologies(base, INHERITED)
    originals = {p: p.read_bytes() for p in (lower_file, base_file)}
    top.mkdir()
    bases = [lower, base]
    if control == "populated":
        put_ideologies(top, "# Writable wins\nideologies = { local = { future = yes } }\n")
    elif control == "empty":
        put_ideologies(top, "")
    elif control == "suppressed":
        (top / "descriptor.mod").write_text('replace_path="common/ideologies"')
    elif control == "base_suppressed":
        suppressor = tmp_path / "suppressor"
        suppressor.mkdir()
        (suppressor / "descriptor.mod").write_text('replace_path="common/ideologies"')
        bases.append(suppressor)
    # A writable definition elsewhere lets update exercise an authoritative or
    # suppressed destination without resurrecting an unavailable inherited ID.
    if operation == "update" and control != "missing":
        put_ideologies(top, "ideologies = { first = { color = { 1 2 3 } } }\n",
                       Path("common/ideologies/other.txt"))
    mod = Mod(top, base_mod_paths=bases)
    edited_id = "first" if operation == "update" else "created"
    if operation == "update":
        assert mod.update_ideology("first", color=(9, 8, 7), path=IDEOLOGIES_PATH)
    else:
        mod.create_ideology("created", color=(9, 8, 7))
    expected = {edited_id}
    if control == "missing":
        expected |= {"first", "sibling"}
    elif control == "populated":
        expected.add("local")
    preview = mod.preview()
    print(mod.preview_summary())
    print(preview)
    assert "9 8 7" in preview
    if control == "missing":
        assert SIBLING.rstrip() in preview.replace("\n+", "\n")
    if control not in {"populated", "empty"}:
        assert not (top / IDEOLOGIES_PATH).exists()
    assert mod.preview() == preview
    result = mod.save(require_changes=True)
    print(result)
    print(result.written_files)
    assert top / IDEOLOGIES_PATH in result.written_files
    saved = (top / IDEOLOGIES_PATH).read_text(encoding="utf-8")
    assert "9 8 7" in saved and "hidden =" not in saved
    if control == "missing":
        assert saved.startswith("# File header\n")
        assert saved.endswith("# File footer\n")
        assert SIBLING in saved
        assert "future_setting = yes" in saved and "# First comment" in saved
    elif control == "populated":
        assert "# Writable wins" in saved and "local = { future = yes }" in saved
    assert all(p.read_bytes() == original for p, original in originals.items())
    reloaded = Mod(top, base_mod_paths=bases)
    assert set(reloaded.list_ideologies()) == expected
    assert reloaded.get_ideology(edited_id).color == (9, 8, 7)
    assert reloaded.preview() == ""
    # Once saved, the snapshot is writable and ordinary deletion must still work.
    if control == "missing":
        assert reloaded.delete_ideology("sibling")
        result = reloaded.save(require_changes=True)
        print(result)
        assert "sibling" not in reloaded.list_ideologies()
        assert all(p.read_bytes() == original for p, original in originals.items())


def test_inherited_ideology_snapshot_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    base = tmp_path / "base"
    source = put_ideologies(base, INHERITED)
    mod = Mod(tmp_path / "mod", base_mod_paths=[base])
    with mod.transaction():
        assert mod.update_ideology("first", color=(9, 8, 7))
        assert "sibling" in mod.preview()
    assert mod.get_ideology("first").color == (1, 2, 3)
    assert mod.preview() == ""
    assert not (mod.mod_root / IDEOLOGIES_PATH).exists()
    assert source.read_text(encoding="utf-8") == INHERITED
