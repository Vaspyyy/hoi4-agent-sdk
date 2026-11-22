"""Opt-in check: the OOB and country checks raise no error on the game's own files.

Skipped unless ``HOI4_INSTALL_DIR`` points at a Hearts of Iron IV install. The
install is only read, never written. Run it with::

    HOI4_INSTALL_DIR="$HOME/.local/share/Steam/steamapps/common/Hearts of Iron IV" \\
        python -m pytest tests/test_corpus_oob.py -m corpus -p no:cacheprovider -q

The test copies the game's country definitions, tags, country histories and
characters into a scratch mod (a "vanilla mirror"); everything else, such as
the OOB files, states and the map, comes from the install through the SDK's
inheritance. It then runs the country checks and the OOB checks of
``Mod.validate()`` and asserts that every error is one of the few real
mistakes in the game's files listed in ``KNOWN_GAME_MISTAKES``. It also
checks the country warnings that used to fire on every valid country.

A run took about 45 seconds on a busy machine (2026-11).
"""

from __future__ import annotations

import os
import shutil
from collections import Counter
from pathlib import Path

import pytest

from hoi4 import Mod

pytestmark = pytest.mark.corpus

COPIED_FOLDERS = (
    "history/countries",
    "common/countries",
    "common/country_tags",
    "common/characters",
    "common/ideas",
)

# Real mistakes in the game's own files (checked against game version
# 1.20.0, 2026-11). Each entry is (code, file name, text in the message).
KNOWN_GAME_MISTAKES: dict[tuple[str, str, str], str] = {
    (
        "missing_oob_reference",
        "ITA - Italy.txt",
        "ITA_aviazione_legionaria",
    ): "1939 history sets oob to a file that does not exist; only the _bba "
    "and _legacy air files do.",
    (
        "missing_mtg_equipment_variant",
        "USA_1939_naval_mtg.txt",
        "variant 'Omaha Class'",
    ): "USA defines Omaha Class as ship_hull_cruiser_2; the file uses "
    "ship_hull_cruiser_1.",
    (
        "missing_mtg_equipment_variant",
        "SOV_1939_naval_mtg.txt",
        "variant 'Kirov Class'",
    ): "SOV defines Kirov Class as ship_hull_cruiser_2; the file uses "
    "ship_hull_cruiser_1.",
    (
        "missing_mtg_equipment_variant",
        "ENG_1939_Naval.txt",
        "variant 'Emerald Class'",
    ): "ENG defines Emerald Class as ship_hull_cruiser_2; the file uses "
    "ship_hull_cruiser_1.",
    (
        "missing_mtg_equipment_variant",
        "ITA_1939_naval_mtg.txt",
        "variant 'Sirena Class'",
    ): "ITA defines Sirena Class as ship_hull_submarine_1 (its 1936 file uses "
    "that); the 1939 file asks for ship_hull_submarine_2.",
    (
        "missing_mtg_equipment_variant",
        "CAN_1939_naval_mtg.txt",
        "variant 'Saguenay Class'",
    ): "Saguenay Class is only defined in the history of QUE, not of CAN.",
    (
        "missing_bba_equipment_variant",
        "ENG_1939_air_bba.txt",
        "variant 'Short Sunderland'",
    ): "ENG defines Short Sunderland as "
    "large_plane_maritime_patrol_plane_airframe_1; the file uses "
    "large_plane_airframe_1.",
    (
        "missing_bba_equipment_variant",
        "SPA_1939_air_bba.txt",
        "variant 'Do 17'",
    ): "GER defines Do 17 as medium_plane_airframe_1; the file uses "
    "small_plane_cas_airframe_0.",
    (
        "missing_bba_equipment_variant",
        "YUG_1939_air_bba.txt",
        "variant 'Do 17'",
    ): "GER defines Do 17 as medium_plane_airframe_1; the file uses "
    "small_plane_cas_airframe_0.",
}


def _install_dir() -> Path | None:
    value = os.environ.get("HOI4_INSTALL_DIR")
    return Path(value).expanduser() if value else None


@pytest.fixture(scope="module")
def mirror(tmp_path_factory: pytest.TempPathFactory) -> Mod:
    game = _install_dir()
    if game is None:
        pytest.skip("set HOI4_INSTALL_DIR to a Hearts of Iron IV install to run the corpus test")
    if not (game / "history" / "units").is_dir():
        pytest.skip(f"HOI4_INSTALL_DIR={game} has no history/units folder")
    root = tmp_path_factory.mktemp("vanilla_mirror")
    for folder in COPIED_FOLDERS:
        shutil.copytree(game / folder, root / folder)
    (root / "descriptor.mod").write_text(
        'name="vanilla mirror"\nversion="1"\nsupported_version="1.*"\n',
        encoding="utf-8",
    )
    return Mod(root, hoi4_install=game)


def _known(code: str, file_path: str | None, message: str) -> bool:
    name = Path(file_path).name if file_path else ""
    return any(
        code == known_code and name == known_file and text in message
        for known_code, known_file, text in KNOWN_GAME_MISTAKES
    )


def test_game_oobs_and_countries_raise_no_false_error(mirror: Mod) -> None:
    known_parties = set(mirror._vanilla_ideologies) | set(mirror._ideologies)
    findings = [
        *mirror._validate_countries(known_parties),
        *mirror._validate_country_oobs(),
    ]

    assert all(finding.code for finding in findings)
    unexpected = [
        f"{finding.code} {Path(finding.file_path or '').name}:{finding.line} {finding.message}"
        for finding in findings
        if finding.severity == "error"
        and not _known(finding.code, finding.file_path, finding.message)
    ]
    assert unexpected == []

    by_code = Counter(finding.code for finding in findings)
    # These fired on valid countries before: names given per ideology,
    # civil-war tags, and hidden ideas.
    assert by_code["missing_country_name"] == 0
    # A leader of another party is normal; only advice remains.
    assert all(
        finding.severity == "info"
        for finding in findings
        if finding.code == "leader_party_mismatch"
    )
    # Every known mistake is still found, so the list stays honest.
    for code, file_name, text in KNOWN_GAME_MISTAKES:
        assert any(
            finding.code == code
            and Path(finding.file_path or "").name == file_name
            and text in finding.message
            for finding in findings
        ), (code, file_name, text)
