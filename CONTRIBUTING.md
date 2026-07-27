# Contributing

## Setup

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -e '.[dev,map]'
```

## Required checks

```bash
python -m pytest tests/ --cov=hoi4 --cov-branch --cov-fail-under=80
python -m ruff check src/ scripts/ tests/
python -m mypy src/
python -m build
```

Changes to a serializer must include all three regression checks:

1. unchanged input serializes byte-for-byte identically;
2. a one-field edit retains comments, unknown keys, and repeated blocks;
3. the rendered output parses and validates.

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
