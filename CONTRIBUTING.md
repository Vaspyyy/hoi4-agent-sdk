# Contributing

## Setup

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -e '.[dev,map]'
```

With `uv`: `uv venv .venv && uv pip install --python .venv/bin/python -e '.[dev,map,assets]'`.

## Required checks

```bash
python -m pytest tests/ --cov=hoi4 --cov-branch --cov-fail-under=80
python -m ruff check src/ scripts/ tests/
python -m mypy src/
python -m build
```

A change to the script reader (`hoi4.syntax`, `hoi4.textio`, `hoi4.patching`,
`hoi4.parser`) or to any loader must also pass the opt-in corpus test against
a real game install. It only reads the install:

```bash
HOI4_INSTALL_DIR="/path/to/Hearts of Iron IV" python -m pytest -m corpus -p no:cacheprovider -q
```

It checks every `.txt`, `.gfx` and `.gui` file under `common/`, `history/`,
`events/`, `interface/` and `map/`: bytes round trip exactly through
`hoi4.textio`, the syntax tree accounts for every character, the only problems
are the unbalanced braces the game ships, `repair()` fixes those, and
`parse_pdx(text, strict=False)` output parses back to an equivalent tree. Set
`HOI4_CORPUS_LIMIT=N` to check only the first N files (sorted by path). Without
`HOI4_INSTALL_DIR` the test is skipped.

Code that reads or writes script follows the rules in
[docs/internals/parser.md](docs/internals/parser.md):

- Read structure from the syntax tree (`hoi4.syntax.parse`). Never use a
  regular expression over raw script: it cannot tell a comment, a quoted
  string or a nested block from the real thing.
- Write by replacing the span of what changed (`hoi4.patching`). Never render
  a whole existing file again to change one field.
- Read and write script files only through `hoi4.textio`
  (`read_text`/`decode_text` in, `encode_text(text, like=original_bytes)`
  out). Never pass `errors="ignore"`.

Changes to a serializer must include these regression checks:

1. unchanged input serializes byte-for-byte identically;
2. a one-field edit changes only that field's value, and retains comments,
   unknown keys, and repeated blocks;
3. the file keeps its encoding, byte-order mark and line endings;
4. the rendered output parses and validates.

Use temporary directories for tests. Never write fixtures into a real game or
mod directory. For a release candidate, also run the opt-in real-mod smoke gate
against a representative local mod and inspect its preview without saving.

Property tests use 200 deterministic examples in normal CI. The weekly private
audit selects 2,000 with `HOI4_HYPOTHESIS_PROFILE=weekly`.

Tag releases are generated only by `.github/workflows/release.yml`. A manual
workflow dispatch is a dry run: it validates the tag/version/changelog identity,
builds and smoke-tests both distributions, and verifies checksums without
publishing.

New dependencies must be optional unless they are required by the core content
model. UI-framework dependencies do not belong in this package.
