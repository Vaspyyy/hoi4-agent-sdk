from hoi4 import Mod
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
