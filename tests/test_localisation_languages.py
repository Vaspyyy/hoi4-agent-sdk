"""Multi-language localisation: loading, editing, replace/, copying, validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4 import Character, Mod
from hoi4.localisation import (
    LOCALISATION_LANGUAGES,
    localisation_file_name,
    localisation_header_language,
    localisation_language_for_path,
    normalize_localisation_language,
    parse_localization_file,
    serialize_localization_file,
    write_localization_file,
)

BOM = b"\xef\xbb\xbf"


def write_loc(root: Path, language: str, name: str, body: str, *, header: str | None = None) -> Path:
    path = root / "localisation" / language / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(BOM + f"l_{header or language}:\n{body}".encode("utf-8"))
    return path


# ── Module helpers ───────────────────────────────────────────────


def test_supported_languages_constant() -> None:
    assert LOCALISATION_LANGUAGES == (
        "english",
        "braz_por",
        "french",
        "german",
        "japanese",
        "polish",
        "russian",
        "spanish",
        "simp_chinese",
    )


@pytest.mark.parametrize(
    ("raw", "expected"), [("german", "german"), ("l_french", "french"), (" Simp_Chinese ", "simp_chinese")]
)
def test_normalize_language(raw: str, expected: str) -> None:
    assert normalize_localisation_language(raw) == expected


@pytest.mark.parametrize("raw", ["klingon", "", "l_", "italian"])
def test_normalize_language_rejects_unknown(raw: str) -> None:
    with pytest.raises(ValueError, match="Unsupported HOI4 localisation language"):
        normalize_localisation_language(raw)


def test_header_and_path_helpers() -> None:
    assert localisation_header_language("﻿# comment\n\nl_german:\n x:0 \"y\"\n") == "german"
    assert localisation_header_language(" l_braz_por: # note\n") == "braz_por"
    assert localisation_header_language(' key:0 "value"\n') is None
    assert localisation_language_for_path(Path("localisation/russian/replace/a_l_russian.yml")) == "russian"
    assert localisation_language_for_path(Path("/x/localisation/a.yml")) is None
    assert localisation_file_name("events_l_english", "polish") == "events_l_polish.yml"
    assert localisation_file_name("events", "spanish") == "events_l_spanish.yml"


def test_serialize_new_file_uses_language_header(tmp_path: Path) -> None:
    assert serialize_localization_file({"a": "b"}).startswith("l_english:\n")
    assert serialize_localization_file({"a": "b"}, language="japanese").startswith(
        "l_japanese:\n"
    )
    path = tmp_path / "localisation" / "french" / "x_l_french.yml"
    write_localization_file(path, {"k": "v"})
    raw = path.read_bytes()
    assert raw.startswith(BOM + b"l_french:\n")


# ── Mod API ──────────────────────────────────────────────────────


def test_english_default_is_unchanged(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.set_loc("KEY", "Value")
    assert mod.get_loc("KEY") == "Value"
    mod.save()
    raw = (tmp_path / "localisation" / "english" / "mod_l_english.yml").read_bytes()
    assert raw == BOM + b'l_english:\n KEY:0 "Value"\n'
    assert not (tmp_path / "localisation" / "german").exists()


def test_set_loc_other_language_writes_its_own_file(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.set_loc("KEY", "English")
    mod.set_loc("KEY", "Deutsch", language="german")
    mod.set_loc("KEY", "Polski", language="l_polish")
    assert mod.get_loc("KEY") == "English"
    assert mod.get_loc("KEY", language="german") == "Deutsch"
    assert "localization (german): 1 key(s)" in mod.preview_summary()
    mod.save()
    german = tmp_path / "localisation" / "german" / "mod_l_german.yml"
    assert german.read_bytes() == BOM + 'l_german:\n KEY:0 "Deutsch"\n'.encode()
    polish = tmp_path / "localisation" / "polish" / "mod_l_polish.yml"
    assert polish.read_bytes().startswith(BOM + b"l_polish:\n")

    reloaded = Mod(tmp_path)
    assert reloaded.get_loc("KEY") == "English"
    assert reloaded.get_loc("KEY", language="german") == "Deutsch"
    assert reloaded.get_loc("KEY", language="polish") == "Polski"
    assert reloaded.get_loc("KEY", language="french") is None


def test_loading_keeps_languages_separate(tmp_path: Path) -> None:
    write_loc(tmp_path, "english", "a_l_english.yml", ' SHARED:0 "Hello"\n EN_ONLY:0 "Only"\n')
    write_loc(tmp_path, "german", "a_l_german.yml", ' SHARED:0 "Hallo"\n DE_ONLY:0 "Nur"\n')
    write_loc(tmp_path, "german", "nested/b_l_german.yml", ' NESTED:0 "Tief"\n')
    mod = Mod(tmp_path)
    assert mod.all_loc() == {"SHARED": "Hello", "EN_ONLY": "Only"}
    assert mod.all_loc(language="german") == {
        "SHARED": "Hallo",
        "DE_ONLY": "Nur",
        "NESTED": "Tief",
    }
    assert mod.search_loc("hal", language="german") == {"SHARED": "Hallo"}
    assert mod.search_loc("hal") == {}
    assert mod.get_loc("DE_ONLY") is None


def test_set_loc_destination_must_match_language(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    with pytest.raises(ValueError, match="localisation/german"):
        mod.set_loc("K", "V", "localisation/english/x_l_english.yml", language="german")
    with pytest.raises(ValueError, match="localisation/english"):
        mod.set_loc("K", "V", "localisation/german/x_l_german.yml")
    with pytest.raises(ValueError, match="Unsupported"):
        mod.set_loc("K", "V", language="klingon")
    mod.set_loc("K", "V", "localisation/german/custom/x_l_german.yml", language="german")
    mod.save()
    assert (tmp_path / "localisation/german/custom/x_l_german.yml").read_bytes().startswith(
        BOM + b"l_german:"
    )


def test_edit_existing_foreign_file_preserves_source(tmp_path: Path) -> None:
    original = (
        "# translator notes\n"
        ' KEEP:0 "Unverändert" # inline\n'
        ' DUP:0 "first"\n'
        ' DUP:0 "second"\n'
        ' EDIT:0 "alt"\n'
    )
    path = write_loc(tmp_path, "german", "x_l_german.yml", original)
    before = path.read_bytes()

    # Unchanged input is byte-identical (nothing to save).
    mod = Mod(tmp_path)
    assert mod.get_loc("EDIT", language="german") == "alt"
    with pytest.warns(RuntimeWarning, match="No changes"):
        assert mod.save().no_changes
    assert path.read_bytes() == before

    # One-field edit keeps comments and repeated entries.
    mod = Mod(tmp_path)
    mod.set_loc("EDIT", "neu", language="german")
    mod.save()
    assert path.read_bytes() == before.replace(b'EDIT:0 "alt"', b'EDIT:0 "neu"')
    assert parse_localization_file(path)["EDIT"] == "neu"
    assert not [
        issue for issue in Mod(tmp_path).validate() if issue.code.startswith("localization")
    ]


def test_delete_loc_is_language_scoped(tmp_path: Path) -> None:
    write_loc(tmp_path, "english", "a_l_english.yml", ' K:0 "en"\n')
    write_loc(tmp_path, "german", "a_l_german.yml", ' K:0 "de"\n OTHER:0 "x"\n')
    mod = Mod(tmp_path)
    assert mod.delete_loc("K", language="german")
    assert not mod.delete_loc("K", language="german")
    assert mod.get_loc("K") == "en"
    mod.save()
    reloaded = Mod(tmp_path)
    assert reloaded.get_loc("K", language="german") is None
    assert reloaded.get_loc("OTHER", language="german") == "x"
    assert reloaded.get_loc("K") == "en"


def test_replace_folder_routing(tmp_path: Path) -> None:
    write_loc(tmp_path, "german", "a_l_german.yml", ' K:0 "alt"\n')
    mod = Mod(tmp_path)
    mod.set_loc("VANILLA_KEY", "Override", replace=True)
    mod.set_loc("K", "ersetzt", language="german", replace=True)
    mod.set_loc(
        "OTHER",
        "x",
        "localisation/french/replace/custom_l_french.yml",
        language="french",
        replace=True,
    )
    with pytest.raises(ValueError, match="replace"):
        mod.set_loc("BAD", "x", "localisation/french/a_l_french.yml", language="french", replace=True)
    mod.save()
    english_replace = tmp_path / "localisation/english/replace/mod_replace_l_english.yml"
    german_replace = tmp_path / "localisation/german/replace/mod_replace_l_german.yml"
    assert english_replace.read_bytes() == BOM + b'l_english:\n VANILLA_KEY:0 "Override"\n'
    assert german_replace.read_bytes() == BOM + b'l_german:\n K:0 "ersetzt"\n'
    assert (tmp_path / "localisation/french/replace/custom_l_french.yml").is_file()
    # The key moved out of its old file, which now has no entries left.
    assert not (tmp_path / "localisation/german/a_l_german.yml").exists()
    reloaded = Mod(tmp_path)
    assert reloaded.get_loc("K", language="german") == "ersetzt"
    assert reloaded.get_loc("VANILLA_KEY") == "Override"


def test_layered_languages_are_inherited_without_mixing(tmp_path: Path) -> None:
    game, base, top = (tmp_path / name for name in ("game", "base", "top"))
    write_loc(game, "english", "game_l_english.yml", ' GAME:0 "Game"\n')
    write_loc(game, "german", "game_l_german.yml", ' GAME:0 "Spiel"\n')
    write_loc(base, "german", "base_l_german.yml", ' BASE:0 "Basis"\n')
    write_loc(top, "german", "top_l_german.yml", ' TOP:0 "Oben"\n')
    mod = Mod(top, hoi4_install=game, base_mod_paths=[base])
    assert mod.get_loc("GAME") == "Game"
    assert mod.get_loc("GAME", language="german") == "Spiel"
    assert mod.get_loc("BASE", language="german") == "Basis"
    assert mod.get_loc("TOP", language="german") == "Oben"
    assert mod.get_loc("BASE") is None
    mod.set_loc("TOP", "Neu", language="german")
    mod.save()
    assert parse_localization_file(top / "localisation/german/top_l_german.yml") == {"TOP": "Neu"}
    # Inherited files are not materialized when untouched.
    assert not (top / "localisation/german/base_l_german.yml").exists()


def test_copy_loc_to_languages_fills_gaps_from_english(tmp_path: Path) -> None:
    write_loc(tmp_path, "english", "events_l_english.yml", ' A:0 "Alpha"\n B:0 "Beta"\n')
    write_loc(tmp_path, "english", "replace/vanilla_l_english.yml", ' R:0 "Repl"\n')
    write_loc(tmp_path, "german", "events_l_german.yml", ' A:0 "Alpha (de)"\n')
    mod = Mod(tmp_path)
    written = mod.copy_loc_to_languages(languages=["german", "french"])
    assert written == {"german": ["B", "R"], "french": ["A", "B", "R"]}
    assert mod.get_loc("A", language="german") == "Alpha (de)"
    assert mod.get_loc("B", language="german") == "Beta"
    mod.save()
    german = parse_localization_file(tmp_path / "localisation/german/events_l_german.yml")
    assert german == {"A": "Alpha (de)", "B": "Beta"}
    assert parse_localization_file(
        tmp_path / "localisation/german/replace/vanilla_l_german.yml"
    ) == {"R": "Repl"}
    assert (tmp_path / "localisation/french/events_l_french.yml").read_bytes().startswith(
        BOM + b"l_french:\n"
    )

    mod = Mod(tmp_path)
    assert mod.copy_loc_to_languages(languages=["german"]) == {"german": []}
    assert mod.copy_loc_to_languages(["A"], ["german"], overwrite=True) == {"german": ["A"]}
    assert mod.get_loc("A", language="german") == "Alpha"


def test_copy_loc_to_languages_defaults_and_errors(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.set_loc("ONLY", "One")
    written = mod.copy_loc_to_languages()
    assert set(written) == set(LOCALISATION_LANGUAGES) - {"english"}
    assert all(keys == ["ONLY"] for keys in written.values())
    assert mod.get_loc("ONLY", language="simp_chinese") == "One"
    with pytest.raises(KeyError, match="MISSING"):
        mod.copy_loc_to_languages(["MISSING"], ["german"])
    with pytest.raises(ValueError, match="English"):
        mod.copy_loc_to_languages(languages=["english"])
    mod.save()
    assert (tmp_path / "localisation/braz_por/mod_l_braz_por.yml").is_file()


def test_copy_loc_skips_inherited_english(tmp_path: Path) -> None:
    game, base, top = (tmp_path / name for name in ("game", "base", "top"))
    write_loc(game, "english", "game_l_english.yml", ' GAME:0 "Game"\n')
    base.mkdir()
    write_loc(top, "english", "top_l_english.yml", ' MINE:0 "Mine"\n')
    mod = Mod(top, hoi4_install=game, base_mod_paths=[base])
    assert mod.copy_loc_to_languages(languages=["spanish"]) == {"spanish": ["MINE"]}


def test_validation_reports_header_language_mismatch(tmp_path: Path) -> None:
    path = write_loc(tmp_path, "german", "wrong_l_german.yml", ' K:0 "v"\n', header="english")
    write_loc(tmp_path, "english", "ok_l_english.yml", ' K:0 "v"\n')
    issues = [
        issue
        for issue in Mod(tmp_path).validate()
        if issue.code == "localization_language_mismatch"
    ]
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].file_path == str(path)
    assert "l_german" in issues[0].message


def test_validation_warns_about_missing_translations_only_when_shipped(tmp_path: Path) -> None:
    write_loc(tmp_path, "english", "a_l_english.yml", ' A:0 "a"\n B:0 "b"\n')
    mod = Mod(tmp_path)
    assert not [issue for issue in mod.validate() if issue.code == "missing_translation"]

    write_loc(tmp_path, "russian", "a_l_russian.yml", ' A:0 "а"\n')
    mod = Mod(tmp_path)
    issues = [issue for issue in mod.validate() if issue.code == "missing_translation"]
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert "russian" in issues[0].message and "B" in issues[0].message
    mod.copy_loc_to_languages(languages=["russian"])
    assert not [issue for issue in mod.validate() if issue.code == "missing_translation"]


def test_transaction_rolls_back_foreign_edits(tmp_path: Path) -> None:
    write_loc(tmp_path, "german", "a_l_german.yml", ' K:0 "alt"\n')
    mod = Mod(tmp_path)
    with mod.transaction():
        mod.set_loc("K", "neu", language="german")
        assert mod.get_loc("K", language="german") == "neu"
    assert mod.get_loc("K", language="german") == "alt"
    with pytest.warns(RuntimeWarning, match="No changes"):
        assert mod.save().no_changes


def test_delete_character_removes_shipped_translations(tmp_path: Path) -> None:
    mod = Mod(tmp_path)
    mod.create_character("ABC", Character(id="ABC_hero", name="Hero"), recruit=False)
    mod.copy_loc_to_languages(["ABC_hero", "ABC_hero_desc"], ["german"])
    mod.save()
    mod = Mod(tmp_path)
    assert mod.get_loc("ABC_hero", language="german") == "Hero"
    assert mod.delete_character("ABC_hero")
    assert mod.get_loc("ABC_hero", language="german") is None
    assert mod.get_loc("ABC_hero_desc", language="german") is None


def test_liveness_still_reports_english_only(tmp_path: Path) -> None:
    write_loc(tmp_path, "english", "a_l_english.yml", ' UNUSED:0 "x"\n')
    write_loc(tmp_path, "german", "a_l_german.yml", ' UNUSED:0 "x"\n GERMAN_ONLY:0 "y"\n')
    report = Mod(tmp_path).analyze_content_liveness()
    assert report.unused_localization == ("UNUSED",)
