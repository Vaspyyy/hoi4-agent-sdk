# Claude Code Instructions

Read and follow [`AGENTS.md`](AGENTS.md) before making or advising on any HOI4
mod change. Its workflows and hard rules are mandatory.

In particular, the **Country Visual Completeness** policy applies even when the
user does not mention graphics. A request to create, release, restore, or make a
country independent implicitly includes the complete original flag and
character-portrait package described there. Use Gemini after checking for
`GEMINI_API_KEY` or `GOOGLE_API_KEY`; a broad country request authorizes
generation and import after visual review.

If neither variable exists, do not silently omit the GFX. Tell the user that
proper custom flags and character portraits require a billing-enabled Gemini
API key from <https://aistudio.google.com/>, explain which environment variable
to set, and explicitly report the country as visually incomplete.
