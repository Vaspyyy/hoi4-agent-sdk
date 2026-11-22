"""Opt-in check of the per-object content checks against a real game install.

The game's own files are valid by definition, so the focus, event, idea and
decision checks must report no error and no warning on them, except the few
real mistakes listed in ``KNOWN_GAME_MISTAKES``. Skipped unless
``HOI4_INSTALL_DIR`` points at a Hearts of Iron IV install, which is only
read. Run it with::

    HOI4_INSTALL_DIR="$HOME/.local/share/Steam/steamapps/common/Hearts of Iron IV" \\
        python -m pytest -m corpus tests/test_corpus_content.py -p no:cacheprovider -q

The test copies the game's focus, event, idea and decision folders into a
scratch mod (so they are the mod's own content, like a mod that overrides
them) and calls the validators directly, with the catalogs ``Mod.validate()``
builds. A run takes about a minute.

Strict localisation is checked differently: the game ships some text keys
that exist in no language (unused or unfinished content). For every
``missing_localization`` finding the test confirms that the key is in no
``localisation`` file of any language, so a finding is never a misread key.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from hoi4 import Mod
from hoi4.localisation import YML_ENTRY_RE, normalize_localization_key
from hoi4.mod._shared import _filter_validation_errors
from hoi4.textio import read_text
from hoi4.types import ValidationError
from hoi4.validation import (
    validate_decision,
    validate_event,
    validate_focus_tree,
    validate_idea,
    validate_idea_category,
)

pytestmark = pytest.mark.corpus

FOLDERS = ("events", "common/national_focus", "common/ideas", "common/decisions")

#: Real mistakes in the game's files (HOI4 1.19), as ``(code, file name,
#: text in the message)``. Each is explained in the comment above it.
KNOWN_GAME_MISTAKES = {
    # ``search_filters = { FOCUS_FILTER_INDUSTRIAL }``: no such sprite; every
    # other focus uses FOCUS_FILTER_INDUSTRY.
    ("unknown_focus_search_filter", "paraguay_uruguay_shared_branch.txt", "FOCUS_FILTER_INDUSTRIAL"),
    # ``1010 = { 12413 = { supply_node < 1 } }``: 12413 is a province, not a
    # state, and script has no province scopes.
    ("unknown_state_reference", "iraq.txt", "12413"),
    # ``declare_war_on = { target = event_target:communist_china }`` has no
    # wargoal type.
    ("missing_wargoal_type", "WTT_PRC.txt", "declare_war_on"),
}


def _install_dir() -> Path | None:
    value = os.environ.get("HOI4_INSTALL_DIR")
    return Path(value).expanduser() if value else None


@pytest.fixture(scope="module")
def game_mod(tmp_path_factory: pytest.TempPathFactory) -> Mod:
    install = _install_dir()
    if install is None:
        pytest.skip("set HOI4_INSTALL_DIR to a Hearts of Iron IV install")
    root = tmp_path_factory.mktemp("vanilla_content")
    for folder in FOLDERS:
        shutil.copytree(install / folder, root / folder)
    (root / "descriptor.mod").write_text('name = "vanilla content"\n', encoding="utf-8")
    return Mod(root, hoi4_install=install)


def _content_findings(mod: Mod) -> list[ValidationError]:
    known_state_ids = {entry["id"] for entry in mod.state_index()}
    focus_ids = {focus.id for tree in mod._focus_trees.values() for focus in tree.focuses}
    filters = mod._known_focus_search_filters()
    namespaces = mod._declared_event_namespaces()
    categories = mod._known_idea_categories()
    findings: list[ValidationError] = []
    for tree in mod._focus_trees.values():
        findings += validate_focus_tree(
            tree,
            known_focus_ids=focus_ids,
            known_state_ids=known_state_ids,
            known_search_filters=filters,
        )
        for focus in tree.focuses:
            findings += mod._focus_localization_findings(tree, focus)
    for event in mod._events.values():
        findings += validate_event(event, namespaces=namespaces)
    for idea in mod._ideas.values():
        findings += validate_idea(idea)
        findings += validate_idea_category(idea, categories)
    for decision in mod._decisions.values():
        findings += validate_decision(decision)
    return _filter_validation_errors(findings, advice=False)


def _signature(finding: ValidationError) -> tuple[str, str]:
    return finding.code, Path(finding.file_path or "").name


def test_game_content_has_no_errors_or_warnings(game_mod: Mod):
    focuses = sum(len(tree.focuses) for tree in game_mod._focus_trees.values())
    assert focuses > 5000 and len(game_mod._events) > 5000 and len(game_mod._ideas) > 2000
    findings = _content_findings(game_mod)
    unexplained = [
        finding
        for finding in findings
        if not any(
            (code, name) == _signature(finding) and text in finding.message
            for code, name, text in KNOWN_GAME_MISTAKES
        )
    ]
    assert not unexplained, "\n".join(
        f"{f.severity} {f.code} {Path(f.file_path or '').name}: {f.message}"
        for f in unexplained[:50]
    )
    found = {
        (code, name, text)
        for code, name, text in KNOWN_GAME_MISTAKES
        for finding in findings
        if (code, name) == _signature(finding) and text in finding.message
    }
    assert found == KNOWN_GAME_MISTAKES, "a known game mistake was fixed; update the list"


def test_strict_localization_reports_only_keys_missing_in_every_language(game_mod: Mod):
    install = _install_dir()
    assert install is not None
    every_language: set[str] = set()
    for path in (install / "localisation").rglob("*.yml"):
        for line in read_text(path).splitlines():
            match = YML_ENTRY_RE.match(line)
            if match is not None:
                every_language.add(normalize_localization_key(match.group(1)))
    findings = _filter_validation_errors(
        game_mod._validate_localization_references(), advice=False
    )
    misread = []
    for finding in findings:
        assert finding.code == "missing_localization", finding
        quoted = finding.message.split("'")
        key = quoted[3] if len(quoted) > 3 else ""
        if key in every_language:
            misread.append(finding.message)
    assert not misread, "\n".join(misread[:50])
