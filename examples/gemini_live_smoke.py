"""Explicit, billable live smoke test for one Gemini flag candidate.

This example intentionally does not import the result into a mod.
"""

from __future__ import annotations

import os
from pathlib import Path

from hoi4 import GeminiImageGenerator


if os.environ.get("HOI4_GEMINI_BILLABLE_SMOKE") != "I_UNDERSTAND":
    raise SystemExit(
        "Refusing a billable request. Set "
        "HOI4_GEMINI_BILLABLE_SMOKE=I_UNDERSTAND after supplying a Gemini API key."
    )

destination = Path("/tmp/hoi4-agent-assets/gemini-live-smoke-flag.png")
with GeminiImageGenerator() as generator:
    result = generator.generate_flag_candidate(
        "A fictional Alpine Republic. Use exactly two horizontal bands: uniform dark "
        "forest green above and uniform deep navy blue below. Center one broad white "
        "three-peak mountain silhouette across the boundary and exactly one large flat "
        "gold five-point star above it. Do not add other symbols, bands, or colors.",
        destination,
    )

print(result.path)
print(result.dimensions)
print(result.sha256)
