"""Opt-in check of the script reader against every script file of a real game install.

Skipped unless ``HOI4_INSTALL_DIR`` points at a Hearts of Iron IV install. The
install is only read, never written. Run it with::

    HOI4_INSTALL_DIR="$HOME/.local/share/Steam/steamapps/common/Hearts of Iron IV" \\
        python -m pytest -m corpus -p no:cacheprovider -q

Every ``.txt``, ``.gfx`` and ``.gui`` file under ``common/``, ``history/``,
``events/``, ``interface/`` and ``map/`` (about 5,400 files, 108 MB) is read
once; the checks then report every file that fails each of them:

* the bytes round-trip exactly through :mod:`hoi4.textio`;
* :func:`hoi4.syntax.parse` accounts for every character, with spans that nest;
* the only problems are unclosed blocks and stray closing braces (the game
  ships a few of those), except in the known non-script files of
  ``NON_SCRIPT_FILES``;
* :func:`hoi4.syntax.repair` makes each such file well formed;
* ``parse_pdx(text, strict=False)`` re-serialises to text that parses to an
  equivalent tree.

A full run took about 90 seconds on a busy machine (measured
2026-11). Set ``HOI4_CORPUS_LIMIT`` to check only the first N files (sorted by
path) for a quick look.
"""

from __future__ import annotations

import os
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from _syntax_checks import assert_covers_all_text, assert_spans_nest

from hoi4.parser import PdxNode, parse_pdx, serialize_pdx
from hoi4.syntax import parse, repair
from hoi4.textio import decode_text, encode_text

pytestmark = pytest.mark.corpus

FOLDERS = ("common", "history", "events", "interface", "map")
SUFFIXES = frozenset({".txt", ".gfx", ".gui"})
TOLERATED = frozenset({"unclosed_block", "stray_close"})
# Text files in the scanned folders that are not Paradox script.
NON_SCRIPT_FILES = frozenset({"interface/credits.txt"})


def _install_dir() -> Path | None:
    value = os.environ.get("HOI4_INSTALL_DIR")
    return Path(value).expanduser() if value else None


@dataclass
class CorpusReport:
    files: int = 0
    bytes: int = 0
    seconds: float = 0.0
    problems: Counter[str] = field(default_factory=Counter)
    files_with_problems: list[str] = field(default_factory=list)
    failures: dict[str, list[str]] = field(default_factory=dict)

    def fail(self, check: str, path: str, detail: str) -> None:
        self.failures.setdefault(check, []).append(f"{path}: {detail}")

    def summary(self) -> str:
        kinds = ", ".join(f"{kind} {count}" for kind, count in sorted(self.problems.items()))
        return (
            f"{self.files} files, {self.bytes / 1e6:.1f} MB in {self.seconds:.1f} s; "
            f"problems: {kinds or 'none'} in {len(self.files_with_problems)} files"
        )


def _normalised(node: PdxNode) -> PdxNode:
    """The tree with the ``quoted`` flag cleared on bare words holding ``!``.

    ``serialize_pdx`` quotes such words (the shared quoting rule); the game
    reads both spellings as the same word.
    """

    children = [_normalised(child) for child in node.children]
    quoted = node.quoted and not (node.value is not None and "!" in node.value)
    return PdxNode(
        key=node.key, value=node.value, children=children, operator=node.operator,
        is_comment=node.is_comment, block=node.block, quoted=quoted,
        key_quoted=node.key_quoted, tag=node.tag,
    )


def _check_file(report: CorpusReport, root: Path, path: Path) -> None:
    name = path.relative_to(root).as_posix()
    data = path.read_bytes()
    report.files += 1
    report.bytes += len(data)

    text = decode_text(data)
    if encode_text(text, like=data) != data:
        report.fail("bytes", name, "textio round trip changed the bytes")

    doc = parse(text)
    try:
        assert_spans_nest(doc)
        assert_covers_all_text(doc)
    except AssertionError as error:
        report.fail("coverage", name, str(error)[:200])

    if doc.problems:
        report.files_with_problems.append(name)
    for problem in doc.problems:
        report.problems[problem.kind] += 1
        if problem.kind not in TOLERATED and name not in NON_SCRIPT_FILES:
            line, col = doc.line_col(problem.offset)
            report.fail("problems", name, f"{problem.kind} at {line}:{col}")

    if doc.problems and {p.kind for p in doc.problems} <= TOLERATED:
        repaired = repair(text)
        left = parse(repaired).problems
        if left:
            report.fail("repair", name, f"still {left[0].kind} after repair")

    try:
        tree = parse_pdx(text, strict=False)
        again = parse_pdx(serialize_pdx(tree), strict=False)
        if again != tree and _normalised(again) != _normalised(tree):
            report.fail("pdxnode", name, "re-serialised tree differs")
    except Exception:  # noqa: BLE001 - every crash is a finding
        report.fail("pdxnode", name, traceback.format_exc(limit=1).strip()[-200:])


@pytest.fixture(scope="module")
def corpus() -> CorpusReport:
    root = _install_dir()
    if root is None:
        pytest.skip("set HOI4_INSTALL_DIR to a Hearts of Iron IV install to run the corpus test")
    if not (root / "common").is_dir():
        pytest.skip(f"HOI4_INSTALL_DIR={root} has no common/ folder")
    paths = sorted(
        path
        for folder in FOLDERS
        for path in (root / folder).rglob("*")
        if path.suffix in SUFFIXES and path.is_file()
    )
    limit = os.environ.get("HOI4_CORPUS_LIMIT")
    if limit:
        paths = paths[: int(limit)]
    report = CorpusReport()
    started = time.perf_counter()
    for path in paths:
        _check_file(report, root, path)
    report.seconds = time.perf_counter() - started
    print(f"\ncorpus: {report.summary()}")
    return report


def _assert_check(corpus: CorpusReport, check: str) -> None:
    failures = corpus.failures.get(check, [])
    assert not failures, f"{len(failures)} files fail:\n" + "\n".join(failures[:30])


def test_corpus_was_found(corpus: CorpusReport) -> None:
    assert corpus.files > 0


def test_bytes_round_trip_exactly(corpus: CorpusReport) -> None:
    _assert_check(corpus, "bytes")


def test_parse_accounts_for_every_character(corpus: CorpusReport) -> None:
    _assert_check(corpus, "coverage")


def test_only_brace_problems_outside_known_non_script_files(corpus: CorpusReport) -> None:
    _assert_check(corpus, "problems")


def test_repair_makes_every_file_well_formed(corpus: CorpusReport) -> None:
    _assert_check(corpus, "repair")


def test_pdxnode_round_trip_is_equivalent(corpus: CorpusReport) -> None:
    _assert_check(corpus, "pdxnode")
