# Claude Code Instructions

Read and follow [`AGENTS.md`](AGENTS.md) before making or advising on any HOI4
mod change. Its workflows and hard rules are mandatory.

In particular, the **Country Visual Completeness** policy applies even when the
user does not mention graphics. A request to create, release, restore, or make a
country independent implicitly includes the complete flag and
character-portrait package described there. Prefer suitable existing assets,
then Wikimedia Commons through `CommonsImageClient`. Review identity, date,
license, and full-size/small-size appearance; keep downloads and JSON provenance
in durable `assets/sources/`. A broad country request authorizes download and
import after review. Use existing `Mod` import helpers and preserve attribution
when required. Missing Gemini credentials do not block local or web sourcing.

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

Use optional Gemini generation only when needed or explicitly desired and paid
generation is authorized; a failed web search or an available key does not
provide that authorization. If requested generation lacks credentials, explain
that it requires a billing-enabled Gemini API key from
<https://aistudio.google.com/> in `GEMINI_API_KEY` or `GOOGLE_API_KEY`. Follow
AGENTS.md's durable candidate storage, visual review, and three-candidate limit.
Report any missing graphics explicitly and continue available work.
