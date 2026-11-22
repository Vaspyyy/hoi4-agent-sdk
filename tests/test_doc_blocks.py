"""Run every ``python`` block of the user docs.

The docs are read by people and by AI agents who copy the examples. A block
that fails gives every one of them a wrong start, so each block runs here:

* README.md, AGENTS.md, CLAUDE.md and every page under ``docs/``.
* Blocks of one page run in page order in one namespace, so a later block can
  use what an earlier one made. Each page starts on a fresh copy of a small
  fixture mod and a small fake game folder (``tests/doc_fixtures/``).
* The docs use one spelling for placeholder paths: ``"/path/to/my_mod"`` is the
  mod, ``"/path/to/Hearts of Iron IV"`` is the game (both also as the start
  of a longer path). Any other ``"/path/to/..."`` is a file or folder in a
  scratch folder of the page.
* The blocks run in that scratch folder. It starts as a copy of
  ``doc_fixtures/scratch`` (an error log, a support file to import) and holds
  a ``.hoi4.json`` naming the mod and the game, so ``Mod.from_config()`` finds
  them.
* Every ``.png`` file name the page mentions (``flag.png``,
  ``ZAR_leader_1.png``) exists as a small test image, both in the scratch
  folder and in the mod's ``assets/candidates/`` folder.
* A block that cannot run in a plain test run carries a marker on the line
  before its fence, with the reason::

      <!-- doc-test: skip (needs the installed game) -->

  A marker without a reason fails the test. Skips stay rare (see
  ``MAX_SKIP_SHARE``).
* ``HOI4_DOC_TEST_GAME=<game folder>`` runs every page against that game
  instead of the fake one, and also runs the blocks marked
  ``needs the installed game``. The game is only read. Examples that rely on
  the fake game's small map can fail there; that run is a manual check.

``test_api_signatures_match_the_code`` checks that every public ``Mod`` method
has exactly one signature row in ``docs/api.md`` and that the row matches the
code (``scripts/api_signatures.py``).

``python tests/test_doc_blocks.py`` prints the blocks per file and the skips per
reason.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import traceback
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "doc_fixtures"
DOC_FILES = (
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    *sorted((ROOT / "docs").rglob("*.md")),
)
MOD_PLACEHOLDER = "/path/to/my_mod"
GAME_PLACEHOLDER = "/path/to/Hearts of Iron IV"
GAME_REASON = "needs the installed game"
MAX_SKIP_SHARE = 0.15

FENCE = re.compile(r"^(?P<indent> *)```(?P<lang>[\w+-]*)[^\n]*$")
MARKER = re.compile(r"^<!--\s*doc-test:\s*(?P<body>.*?)\s*-->\s*$")
SKIP = re.compile(r"^skip(?:\s*\((?P<reason>[^)]*)\))?$")
IMAGE_NAME = re.compile(r"[\w.-]+\.png\b")


@dataclass(frozen=True)
class Block:
    path: Path
    line: int  # line of the opening fence, 1-based
    code: str
    skip: str | None  # the skip reason, "" for a marker without one

    @property
    def where(self) -> str:
        return f"{self.path.relative_to(ROOT)}:{self.line}"


def collect(path: Path) -> list[Block]:
    """Return the ``python`` blocks of one Markdown file, in page order."""

    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[Block] = []
    index = 0
    while index < len(lines):
        match = FENCE.match(lines[index])
        if not match:
            index += 1
            continue
        start = index
        indent = match.group("indent")
        end = start + 1
        while end < len(lines) and not lines[end].startswith(f"{indent}```"):
            end += 1
        if match.group("lang") == "python":
            skip = None
            previous = start - 1
            while previous >= 0 and not lines[previous].strip():
                previous -= 1
            marker = MARKER.match(lines[previous].strip()) if previous >= 0 else None
            if marker:
                found = SKIP.match(marker.group("body"))
                if found is None:
                    raise ValueError(f"{path}:{previous + 1}: unknown doc-test marker")
                skip = (found.group("reason") or "").strip()
            body = [line.removeprefix(indent) for line in lines[start + 1 : end]]
            blocks.append(Block(path, start + 1, "\n".join(body), skip))
        index = end + 1
    return blocks


ALL_BLOCKS = [block for path in DOC_FILES if path.exists() for block in collect(path)]
PAGES = sorted({block.path for block in ALL_BLOCKS})


def _prepare(code: str, mod_dir: Path, game_dir: Path, scratch: Path) -> str:
    """Point the placeholder paths of a block at the fixture folders."""

    roots = {MOD_PLACEHOLDER: mod_dir, GAME_PLACEHOLDER: game_dir}

    def resolve(match: re.Match[str]) -> str:
        path = match.group(1)
        for placeholder, root in roots.items():
            if path == placeholder or path.startswith(placeholder + "/"):
                return repr(str(root / path[len(placeholder) :].lstrip("/")))
        return repr(str(scratch / path.removeprefix("/path/to/")))

    return re.sub(r'"(/path/to/[^"]*)"', resolve, code)


def _error(block: Block, exc: BaseException) -> str:
    if isinstance(exc, SyntaxError):
        return f"{block.where}: SyntaxError: {exc.msg} (block line {exc.lineno})"
    frames = traceback.extract_tb(exc.__traceback__)
    line = next((f.lineno for f in reversed(frames) if f.filename == block.where), None)
    at = f" (block line {line})" if line else ""
    return f"{block.where}: {type(exc).__name__}: {exc}{at}"


def _no_network(*_args: object, **_kwargs: object) -> None:
    raise OSError("the doc test has no network")


def _write_images(page: Path, folders: list[Path]) -> None:
    from PIL import Image

    names = sorted(set(IMAGE_NAME.findall(page.read_text(encoding="utf-8"))))
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            Image.new("RGBA", (64, 64), (40, 90, 160, 255)).save(folder / name)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_doc_blocks_run(
    page: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_game = os.environ.get("HOI4_DOC_TEST_GAME")
    mod_dir = tmp_path / "my_mod"
    scratch = tmp_path / "scratch"
    shutil.copytree(FIXTURES / "mod", mod_dir)
    shutil.copytree(FIXTURES / "scratch", scratch)
    if real_game:
        game_dir = Path(real_game)  # only read: every write goes to the mod
    else:
        game_dir = tmp_path / "game"
        shutil.copytree(FIXTURES / "game", game_dir)
    (scratch / ".hoi4.json").write_text(
        json.dumps({"mod_path": str(mod_dir), "hoi4_install": str(game_dir)}), encoding="utf-8"
    )
    _write_images(page, [scratch, mod_dir / "assets" / "candidates"])
    monkeypatch.chdir(scratch)
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "HOI4_GEMINI_BILLABLE_SMOKE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    namespace: dict[str, object] = {"__name__": "__doc__"}
    failures: list[str] = []
    for block in (b for b in ALL_BLOCKS if b.path == page):
        if block.skip is not None and not (block.skip == GAME_REASON and real_game):
            continue
        code = _prepare(block.code, mod_dir, game_dir, scratch)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                exec(compile(code, block.where, "exec"), namespace)
        except BaseException as exc:  # noqa: BLE001 - every error is a doc failure
            failures.append(_error(block, exc))
    assert not failures, "Doc blocks failed:\n" + "\n".join(failures)


def test_api_signatures_match_the_code() -> None:
    """Every public ``Mod`` method has one row in docs/api.md, and it is current.

    Fix with ``python scripts/api_signatures.py --write`` (it rewrites the
    signature cells; a missing row has to be added by hand).
    """

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "api_signatures", ROOT / "scripts" / "api_signatures.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    problems = module.problems((ROOT / "docs" / "api.md").read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


def test_every_skip_has_a_reason() -> None:
    missing = [block.where for block in ALL_BLOCKS if block.skip == ""]
    assert not missing, f"doc-test skip markers without a reason: {missing}"


def test_few_blocks_are_skipped() -> None:
    skipped = [block for block in ALL_BLOCKS if block.skip is not None]
    assert len(skipped) < MAX_SKIP_SHARE * len(ALL_BLOCKS), skip_report()


def skip_report() -> str:
    lines = []
    for path in PAGES:
        blocks = [b for b in ALL_BLOCKS if b.path == path]
        skipped = sum(1 for b in blocks if b.skip is not None)
        lines.append(f"{path.relative_to(ROOT)}: {len(blocks)} blocks, {len(blocks) - skipped} run, {skipped} skipped")
    reasons = Counter(b.skip for b in ALL_BLOCKS if b.skip is not None)
    total = len(ALL_BLOCKS)
    skipped = sum(reasons.values())
    lines.append(f"total: {total} blocks, {total - skipped} run, {skipped} skipped")
    lines.extend(f"  skipped ({reason}): {count}" for reason, count in sorted(reasons.items()))
    return "\n".join(lines)


if __name__ == "__main__":
    print(skip_report())
