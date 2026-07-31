# Claude Code Instructions

Read and follow [`AGENTS.md`](AGENTS.md) before making or advising on any HOI4
mod change. Its workflows and hard rules are mandatory.

In particular, the **Country Visual Completeness** policy applies even when the
user does not mention graphics. A request to create, release, restore, or make a
country independent implicitly includes the complete original flag and
character-portrait package described there. Use Gemini after checking for
`GEMINI_API_KEY` or `GOOGLE_API_KEY`; a broad country request authorizes
generation and import after visual review.

Use the SDK's `Character` role/instance APIs and separate `create_oob()` land,
naval, and air files for the roster and, for countries present at scenario
start, the starting forces; do not hand-write those files. Use DLC-gated
`EquipmentVariant` definitions for Man the Guns hulls and a separately gated
legacy naval fallback. Use
`validate(stage="build")` during a multi-step build and
`validate(stage="release")` for final vocabulary and liveness checks. Later
focus/event releases must have an explicit
runtime territory/capital setup instead of fake 1936 ownership. The country is
not structurally complete until `mod.validate_country_package(tag).complete`
is true. That property and the liveness graph do not prove dynamic popularity
or variable thresholds are achievable; review those chains and live-test them.

If neither variable exists, do not silently omit the GFX. Tell the user that
proper custom flags and character portraits require a billing-enabled Gemini
API key from <https://aistudio.google.com/>, explain which environment variable
to set, and explicitly report the country as visually incomplete.
