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
python -m pip install 'hoi4-agent-sdk[gemini]'
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

- complete-country authoring with source-preserving character rosters, land,
  naval, and air orders of battle, politics, tags, localisation, package reports, and
  enforced release-gate completeness
- states, ownership, cores, resources, buildings, and victory points
- focus trees, events, on-actions, decisions, ideas, and dynamic modifiers
- ideology definitions and bookmark scenarios
- project scaffolding, launcher descriptors, and safe mod discovery
- flag, portrait, and bookmark-picture import/export with optional Pillow support
- optional Gemini generation of reviewable flag and leader-portrait PNG candidates
- political-map rendering and procedural map/province generation
- disconnected-territory detection from the effective HOI4 province map
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

Gemini generation is an optional, billable candidate step. It does not modify a
mod; review the PNG before passing it to the existing import helpers:

```python
from pathlib import Path

from hoi4 import GeminiImageGenerator, import_flag_to_mod

project_root = Path.cwd()  # directory containing .hoi4.json
candidate_path = project_root / "assets" / "candidates" / "ABC-flag-01.png"
with GeminiImageGenerator() as generator:
    candidate = generator.generate_flag_candidate(
        "A blue alpine republic with a white mountain and gold star",
        candidate_path,
    )

# Inspect candidate.path at full size and at 10x7 before importing.
import_flag_to_mod(root, "ABC", candidate.path)
```

Gemini candidates are billed, non-reproducible build inputs. Keep every
candidate under the mod project's durable `assets/candidates/` directory, use a
unique filename for each attempt, and import the accepted PNG from that same
path. Do not put generated candidates in an operating-system temporary
directory: `/tmp` may be memory-backed and wiped on reboot. One-off automation
scripts remain appropriately disposable under `/tmp/hoi4-agent-scripts/`.

Install the `gemini` extra and set `GEMINI_API_KEY` or `GOOGLE_API_KEY`; keys are
never stored by the SDK. The default is `gemini-3.1-flash-image` at 512px.
For reliable flags, describe an exhaustive layout with exact symbol counts,
color roles, and relative symbol size. For portraits, describe the desired
historical subject, year, role, clothing, and expression positively instead of
listing objectionable imagery that should not appear.

Agents using this SDK should treat a broad request to create, release, restore,
or make a country independent as a request for the complete player-facing
package, not merely script files. That package includes the three flag sizes, a
leader portrait, portraits for all new visible characters, at least two
political advisors and two military commanders, historically appropriate
additional officers, and the corresponding DDS/GFX, roles, and localization.
A broad country request authorizes generation and import after visual review.
If no API key is available, the agent must say that proper custom GFX requires a
billing-enabled Gemini API key from [Google AI
Studio](https://aistudio.google.com/), name the supported environment variables,
and report the package as visually incomplete rather than silently omitting it.
Use `Character` plus its role models for every roster entry, use
`create_oob(..., assign=True)` for countries that exist at scenario start, and
finish with
`mod.validate_country_package(tag)`. A country is not ready to report complete
until `CountryPackageReport.complete` is true. Tags created through
`create_country()` are checked automatically by normal validation and the
release gate. The report distinguishes starting countries from tags released
later by focus/event effects, so runtime countries are checked against their
activation path rather than incorrectly against the 1936 ownership map.

## Safety workflow

For existing mods, use the following loop:

1. Load with `strict_loading=True` when skipped or malformed files must stop the
   operation.
2. Make changes inside `mod.transaction()`.
3. Inspect `mod.preview_summary()` and `mod.preview()`.
4. Run `mod.validate(stage="build")` between pipeline steps, then
   `mod.validate(stage="release")` and resolve errors before release.
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
older SDK code edits a known field, including modeled naval or air OOB content.

With a configured HOI4 installation, validation checks effect, trigger, and
modifier names against the shipped documentation. Unknown tokens are warnings,
include installed-game usage counts and a close suggestion, and can be
allowlisted explicitly for intentional extensions. Release-stage validation
also reports unreachable focuses, unfired triggered events, ungranted ideas,
asymmetric flag use, and unused localization through
`mod.analyze_content_liveness()`.

## Development

```bash
python -m pytest tests/
python -m ruff check src/ scripts/ tests/
python -m mypy src/
python -m build
```

See [docs/api.md](docs/api.md), [docs/models.md](docs/models.md), and
[CONTRIBUTING.md](CONTRIBUTING.md) for the detailed API and release checks.
The real-mod gate requires at least six completed domain probes by default and
can require named probes with repeatable `--require-probe` flags. The installed
game audit additionally checks the generated effect and modifier documentation,
requires exact technology-category equality, enables strict loading and asset
validation, and writes text plus JSON reports:

```bash
python scripts/audit_hoi4_install.py /path/to/mod --hoi4-install /path/to/hoi4 \
  --fingerprint-manifest /private/path/mod.sha256.json \
  --error-log "$HOME/.local/share/Paradox Interactive/Hearts of Iron IV/logs/error.log"
```

`scripts/parse_hoi4_log.py` is also available as a focused post-launch build
step. It reports only errors whose referenced files exist in the selected mod,
groups them by class, and can continue from a saved byte offset.

## License

MIT. See [LICENSE](LICENSE).
