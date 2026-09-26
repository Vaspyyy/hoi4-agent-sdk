from pathlib import Path

import pytest

from hoi4 import AirWing, Mod
import hoi4.mod as facade


def test_unchanged_name_pool_render_skips_repeated_full_file_patches(tmp_path, monkeypatch):
    mod = Mod(tmp_path / "mod")
    for tag in ("AAA", "BBB", "CCC"):
        mod.set_country_name_pool(
            tag, male_names=[tag + " Man"], female_names=[tag + " Woman"], surnames=["Family"]
        )
    mod.save(require_changes=True)
    source = mod.mod_root / "common/names/00_generated_names.txt"
    original = source.read_bytes()
    calls = []
    actual = facade.set_block

    def counted(text, key, body):
        calls.append(key)
        return actual(text, key, body)

    monkeypatch.setattr(facade, "set_block", counted)
    for tag in ("AAA", "BBB", "CCC"):
        mod.set_country_name_pool(
            tag, male_names=[tag + " Man"], female_names=[tag + " Woman"], surnames=["Family"]
        )
    assert mod.preview() == ""
    assert calls == []
    mod.set_country_name_pool(
        "BBB", male_names=["Replacement"], female_names=["B Woman"], surnames=["Family"]
    )
    assert "Replacement" in mod.preview()
    assert calls == ["BBB"]
    result = mod.save(require_changes=True)
    assert result.written_files == [source]
    assert b"AAA Man" in source.read_bytes() and b"CCC Man" in source.read_bytes()
    assert original != source.read_bytes()


NAMES_PATH = Path("common/names/00_generated_names.txt")
SIBLINGS = '''# Keep this sibling's formatting.
BBB = { male = { names = { "Boris" } } surnames = { "Family" } }
# A fallback for all other countries.
default = { male = { names = { "Alex" } } surnames = { "Smith" } }
'''
INHERITED = '# Header\nAAA = { male = { names = { "Old" } } }\n' + SIBLINGS


def put_names(root, text):
    path = root / NAMES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def has_missing_name_pool(mod):
    return "missing_country_name_pool" in {
        finding.code
        for finding in mod.validate_country_package("CCC", check_geography=False).findings
    }


@pytest.mark.parametrize("control", ["missing", "populated", "empty", "suppressed", "base_suppressed"])
def test_inherited_name_pool_preview_save_reload(tmp_path, monkeypatch, control):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    lower, base, top = (tmp_path / name for name in ("lower", "base", "mod"))
    lower_file = put_names(lower, 'ZZZ = { male = { names = { "Hidden" } } }\n')
    base_file = put_names(base, INHERITED)
    originals = {p: p.read_bytes() for p in (lower_file, base_file)}
    top.mkdir()
    bases = [lower, base]
    if control == "populated":
        put_names(top, '# Writable wins\nDDD = { surnames = { "Local" } }\n')
    elif control == "empty":
        put_names(top, "")
    elif control == "suppressed":
        (top / "descriptor.mod").write_text('replace_path="common/names"')
    elif control == "base_suppressed":
        suppressor = tmp_path / "suppressor"
        suppressor.mkdir()
        (suppressor / "descriptor.mod").write_text('replace_path="common/names"')
        bases.append(suppressor)
    mod = Mod(top, base_mod_paths=bases)
    mod.create_country("CCC", "Test Country", capital=1)
    mod.create_oob(
        "CCC_air", "CCC", kind="air",
        air_wings=[AirWing(1, "fighter_equipment_0", 12, owner="CCC")],
    )
    # Warm the validation cache before staging the edit.
    assert has_missing_name_pool(mod) == (control != "missing")
    mod.set_country_name_pool("AAA", male_names=["Replacement"], surnames=["New Family"])
    expected = {"AAA"}
    if control == "missing":
        expected |= {"BBB", "default"}
    elif control == "populated":
        expected.add("DDD")
    preview = mod.preview()
    print(mod.preview_summary())
    print(preview)
    assert "Replacement" in preview
    if control not in {"populated", "empty"}:
        assert not (top / NAMES_PATH).exists()
    assert has_missing_name_pool(mod) == (control != "missing")
    assert mod._known_country_name_pool_tags() == expected
    effective_text = dict(mod._effective_script_texts("common/names"))[top / NAMES_PATH]
    result = mod.save(require_changes=True)
    print(result)
    print(result.written_files)
    assert top / NAMES_PATH in result.written_files
    saved = (top / NAMES_PATH).read_text(encoding="utf-8")
    assert saved == effective_text
    assert "Replacement" in saved and "Hidden" not in saved
    if control == "missing":
        assert saved.startswith("# Header\n")
        assert SIBLINGS in saved
    elif control == "populated":
        assert '# Writable wins\nDDD = { surnames = { "Local" } }\n' in saved
    assert all(p.read_bytes() == original for p, original in originals.items())
    reloaded = Mod(top, base_mod_paths=bases)
    assert reloaded._known_country_name_pool_tags() == expected
    assert has_missing_name_pool(reloaded) == has_missing_name_pool(mod)
    assert reloaded.preview() == ""


def test_pending_name_pool_validation_cache_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    base = tmp_path / "base"
    put_names(base, INHERITED)
    mod = Mod(tmp_path / "mod", base_mod_paths=[base])
    expected = {"AAA", "BBB", "default"}
    assert mod._known_country_name_pool_tags() == expected
    with mod.transaction():
        mod.set_country_name_pool("DDD", male_names=["New"], surnames=["Family"])
        assert mod._known_country_name_pool_tags() == expected | {"DDD"}
        assert "DDD" in mod.preview()
    assert mod._known_country_name_pool_tags() == expected
    assert mod.preview() == ""
