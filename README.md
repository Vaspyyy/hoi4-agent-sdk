# HOI4 Agent SDK

A source-preserving Python toolkit for creating, inspecting, validating, and
editing Hearts of Iron IV mods. It is designed for automation, desktop tools,
and coding agents that must work safely with both generated files and large
hand-authored mods.

The SDK treats existing Paradox Script as source code: unchanged files round
trip byte-for-byte, edited fields are patched locally, unknown keys and comments
are retained, and writes are committed atomically.

## Installation

```bash
python -m pip install hoi4-agent-sdk
```

Optional image and map tooling is installed separately:

```bash
python -m pip install 'hoi4-agent-sdk[assets]'
python -m pip install 'hoi4-agent-sdk[map]'
```

Python 3.11 or newer is required.

For development from a source checkout, install the editable development and
map extras with `python -m pip install -e '.[dev,map]'`.

## Quick start

```python
from hoi4 import Focus, Mod

mod = Mod("/path/to/my_mod", hoi4_install="/path/to/Hearts of Iron IV")

with mod.transaction():
    mod.add_focus(
        "my_focus_tree",
        Focus(
            id="ABC_expand_industry",
            x=3,
            y=2,
            completion_reward="add_political_power = 50",
        ),
    )
    print(mod.preview_summary())
    print(mod.preview())
    issues = mod.validate()

# Nothing was written. Use transaction(save=True), or call mod.save(), after
# reviewing the preview and validation result.
```

## Supported content

- countries, characters, politics, tags, and localisation
- states, ownership, cores, resources, buildings, and victory points
- focus trees, events, on-actions, decisions, ideas, and dynamic modifiers
- ideology definitions and bookmark scenarios
- project scaffolding, launcher descriptors, and safe mod discovery
- flag, portrait, and bookmark-picture import/export with optional Pillow support
- political-map rendering and procedural map/province generation
- structured validation, duplicate-ID diagnostics, previews, transactions, and
  atomic multi-file saves

## Project and asset services

Project creation is available before a `Mod` exists:

```python
from pathlib import Path

from hoi4.project import create_mod_structure, scan_mod_descriptors, write_mod_descriptors

root = Path("/path/to/mods/my_mod")
launcher_mod_dir = Path("/path/to/launcher/mod")
create_mod_structure(root)
write_mod_descriptors(root, launcher_mod_dir, "My Mod")
discovered = scan_mod_descriptors(
    launcher_mod_dir,
    allowed_roots=[root.parent],
)
```

The explicit `allowed_roots` entry trusts only the intended project parent;
secure discovery continues to reject other external absolute paths.

Flag and portrait helpers import Pillow only when used:

```python
from hoi4 import import_flag_to_mod, import_portrait_to_mod, write_portrait_gfx

import_flag_to_mod(root, "ABC", "flag.png")
import_portrait_to_mod(root, "ABC", "alice", "alice.png")
write_portrait_gfx(root, "ABC", "alice")
```

## Safety workflow

For existing mods, use the following loop:

1. Load with `strict_loading=True` when skipped or malformed files must stop the
   operation.
2. Make changes inside `mod.transaction()`.
3. Inspect `mod.preview_summary()` and `mod.preview()`.
4. Run `mod.validate()` and resolve errors.
5. Save only after the preview is acceptably small and semantically correct.

`Mod` detects source files changed by another writer after it was loaded and
raises `ExternalModificationError` before preview/save. Use one instance per
operation or worker; call `reload()` before reapplying an edit after a conflict.

Country politics are data-driven: custom ideology groups and their popularity
keys are retained. Existing leader edits follow the recruited character ID and
patch that exact block instead of replacing it with a generated leader.

Unsafe duplicate IDs in single-definition domains are reported instead of
silently allowing the last file to win. Intentional repeated on-action hooks and
cross-file decision-category extensions remain compositional. Existing source
is kept alongside structured models so new game keys do not disappear when
older SDK code edits a known field.

## Development

```bash
python -m pytest tests/
python -m ruff check src/ tests/
python -m mypy src/
python -m build
```

See [docs/api.md](docs/api.md), [docs/models.md](docs/models.md), and
[CONTRIBUTING.md](CONTRIBUTING.md) for the detailed API and release checks.
The real-mod gate requires at least six completed domain probes by default and
revalidates every dry-run mutation while verifying that the mod tree remains
unchanged. Run `python scripts/verify_real_mod.py /path/to/mod`; use
`--min-probes` only for an intentionally narrow fixture.

## License

MIT. See [LICENSE](LICENSE).
