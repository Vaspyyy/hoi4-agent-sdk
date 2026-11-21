# HOI4 Agent SDK Website

Documentation, interactive architecture explorer, live API & HOI4 1.19 catalog workbench, release chronicle, and interactive Field Kit for `hoi4-agent-sdk`.

All documentation pages, `Mod` layer metadata, method signatures, `DEFINITION_KINDS`, `VALIDATION_CODES`, curated effects/modifiers, technology categories, and `CHANGELOG.md` entries are extracted directly from the repository by `scripts/extract_sdk_data.py`.

## Commands

```bash
cd website
npm install
npm run dev      # extracts live SDK data and starts Vite dev server
npm run build    # extracts live SDK data, runs tsc --noEmit, and builds dist/
npm run preview  # serves the production build locally
```

Set `VITE_BASE=/hoi4-agent-sdk/` when deploying under a GitHub Pages repository subpath (configured automatically in `.github/workflows/pages.yml`).
