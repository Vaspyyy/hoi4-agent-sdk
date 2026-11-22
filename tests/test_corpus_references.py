"""Opt-in check of the reference checks and the liveness graph against a real game install.

Skipped unless ``HOI4_INSTALL_DIR`` points at a Hearts of Iron IV install. The
install is only read, never written. Run it with::

    HOI4_INSTALL_DIR="$HOME/.local/share/Steam/steamapps/common/Hearts of Iron IV" \\
        python -m pytest -m corpus tests/test_corpus_references.py -p no:cacheprovider -q

The game's own files are valid by definition, so a mod made of copies of
them (a "vanilla mirror") must not raise false alarms:

* the reference checks report no error or warning except the real mistakes
  in the game's files listed in ``KNOWN_GAME_MISTAKES``;
* an empty mod over the game gets no liveness finding: inherited content is
  never reported;
* on the mirror, every liveness finding is confirmed by a plain search of
  the game's comment-free script and English localisation: a triggered-only
  event nobody fires is named only where it is defined, a flag that is set
  and never read is never named next to a reader, and so on. A missed way of
  reaching something would show up here as a finding the search refutes.

A full run takes a few minutes (it copies about 45 MB and reads the whole
game twice).
"""

from __future__ import annotations

import os
import re
import shutil
from collections import defaultdict
from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.syntax import parse
from hoi4.textio import read_text

pytestmark = pytest.mark.corpus

MIRRORED = (
    "events",
    "common/national_focus",
    "common/decisions",
    "common/ideas",
    "common/on_actions",
    "common/scripted_effects",
    "common/scripted_triggers",
    "common/characters",
    "common/countries",
    "common/country_tags",
    "common/dynamic_modifiers",
    "common/bookmarks",
    "common/ideologies",
    "history/countries",
)

# Real mistakes in the game's files (checked 2026-11, game 1.17): names that
# exist nowhere, or only with different upper and lower case. An event's
# namespace is matched without regard to case, as the game does, so
# ``PER_SSB_events.8`` finds ``PER_ssb_events.8``.
KNOWN_GAME_MISTAKES = frozenset(
    {
        ("unknown_event_reference", "EST_events.3"),
        ("unknown_event_reference", "mtg_usa_civil_war_fascists.1000"),
        ("unknown_event_reference", "NSB_news.201"),
        ("unknown_idea_reference", "CHI_claire_lee_chennault_air_chief"),
        ("unknown_idea_reference", "por_portuguese_legion"),
        ("unknown_idea_reference", "SWI_saint_bernard_idea_desc"),
        ("unknown_idea_reference", "ENG_guided_by_britain_RAJ_1"),
        ("unknown_idea_reference", "ENG_guided_by_britain_RAJ_2"),
        ("unknown_idea_reference", "ITA_strengthened_the_airforce_lion_Version"),
        ("unknown_idea_reference", "Hun_treaty_of_triannon_3"),
    }
)
_NAME_RE = re.compile(r"'([^']+)'")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_.@:]+")


def _install_dir() -> Path | None:
    value = os.environ.get("HOI4_INSTALL_DIR")
    return Path(value).expanduser() if value else None


@pytest.fixture(scope="module")
def game() -> Path:
    install = _install_dir()
    if install is None or not install.is_dir():
        pytest.skip("set HOI4_INSTALL_DIR to a Hearts of Iron IV install")
    return install


@pytest.fixture(scope="module")
def mirror(game: Path, tmp_path_factory: pytest.TempPathFactory) -> Mod:
    root = tmp_path_factory.mktemp("vanilla_mirror")
    for relative in MIRRORED:
        source = game / relative
        if source.is_dir():
            shutil.copytree(source, root / relative)
    (root / "descriptor.mod").write_text('name="mirror"\n', encoding="utf-8")
    return Mod(root, hoi4_install=game)


def _reported_name(message: str) -> str:
    names = _NAME_RE.findall(message)
    return names[1] if len(names) > 1 else names[0]


def test_reference_checks_raise_no_false_alarm_on_the_game(mirror: Mod) -> None:
    findings = mirror._validate_script_references(
        known_ideas=set(),
        known_events=set(),
        known_technologies=set(),
        known_equipment=set(),
        known_focus_trees=set(),
    )
    findings += mirror._validate_state_effect_assumptions()
    findings += mirror._validate_idea_mutation_collisions()
    alarms = [finding for finding in findings if finding.severity in {"error", "warning"}]
    unexpected = [
        f"{finding.code} {finding.file_path}:{finding.line} {finding.message}"
        for finding in alarms
        if (finding.code, _reported_name(finding.message)) not in KNOWN_GAME_MISTAKES
    ]
    assert unexpected == []
    assert all(finding.file_path and finding.line for finding in findings)


def test_inherited_content_is_never_reported_as_dead(game: Path, tmp_path: Path) -> None:
    report = Mod(tmp_path / "empty_mod", hoi4_install=game).analyze_content_liveness()
    assert [finding for finding in report.findings if finding.code != "unused_localization"] == []


def _game_token_lines(game: Path, wanted: set[str]) -> dict[str, list[str]]:
    """Every comment-free line of game script or English localisation naming a wanted token."""

    lines: dict[str, list[str]] = defaultdict(list)
    paths = [
        path
        for top in ("common", "events", "history")
        for path in (game / top).rglob("*.txt")
    ]
    paths += list((game / "localisation").rglob("*english*.yml"))
    for path in paths:
        try:
            text = read_text(path)
        except (OSError, ValueError):
            continue
        if path.suffix == ".txt":
            text = parse(text).without_comments()
        for line in text.splitlines():
            for token in set(_TOKEN_RE.findall(line)) & wanted:
                lines[token].append(line.strip())
    return lines


def test_liveness_findings_on_the_game_are_confirmed_by_a_text_search(
    mirror: Mod, game: Path
) -> None:
    findings = [
        finding
        for finding in mirror.analyze_content_liveness().findings
        if finding.code in {"unfired_event", "flag_set_never_read", "flag_read_never_set"}
    ]
    names = {_NAME_RE.findall(finding.message)[0]: finding.code for finding in findings}
    lines = _game_token_lines(game, set(names))
    refuted: list[str] = []
    for name, code in names.items():
        found = lines.get(name, [])
        if code == "unfired_event":
            # Named only once, where it is defined: "id = <name>".
            if len(found) > 1 or any(
                not re.fullmatch(rf"id\s*=\s*{re.escape(name)}", line) for line in found
            ):
                refuted.append(f"{code} {name}: {found[:3]}")
        elif code == "flag_set_never_read":
            if any(re.search(r"\bhas_\w*_flag\s*=", line) or "[?" in line for line in found):
                refuted.append(f"{code} {name}: {found[:3]}")
        elif any(re.search(r"\b(?:set|modify)_\w*flag\b", line) for line in found):
            refuted.append(f"{code} {name}: {found[:3]}")
    assert refuted == [], "\n".join(refuted)
