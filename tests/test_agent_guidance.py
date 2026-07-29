from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GEMINI_GUIDANCE_FILES = (
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "docs" / "api.md",
    ROOT / "docs" / "recipes.md",
    ROOT / "examples" / "gemini_live_smoke.py",
)


def test_country_creation_guidance_requires_complete_gfx_package() -> None:
    guidance = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    required_contract = (
        "Country Visual Completeness",
        "implicitly includes a complete player-facing country package",
        "at least two political advisors and two military commanders",
        "https://aistudio.google.com/",
        "billing-enabled Gemini API key",
        "Do not silently omit the graphics",
        "create_oob(..., assign=True)",
        "does not exist at scenario start",
        "as `runtime`",
        "CountryPackageReport.complete",
    )
    for requirement in required_contract:
        assert requirement in guidance


def test_country_creation_guidance_is_shipped_in_source_distribution() -> None:
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "include AGENTS.md" in manifest
    assert "include CLAUDE.md" in manifest


def test_idea_picture_guidance_requires_bare_stems() -> None:
    guidance = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "picture = <bare_stem>" in guidance
    assert "never `picture = GFX_idea_<stem>`" in guidance
    assert "HOI4 prepends `GFX_idea_` itself" in guidance
    assert "picture = generic_political_support" in guidance
    assert "GFX_idea_generic_political_support" in guidance


def test_claude_is_directed_to_the_country_visual_contract() -> None:
    guidance = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "Country Visual Completeness" in guidance
    assert "implicitly includes the complete original flag" in guidance
    assert "https://aistudio.google.com/" in guidance
    assert "billing-enabled Gemini" in guidance
    assert "Character" in guidance
    assert "create_oob()" in guidance
    assert "runtime territory/capital setup" in guidance
    assert "validate_country_package(tag).complete" in guidance


def test_billable_image_candidates_use_durable_project_storage() -> None:
    guidance = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "assets/candidates/" in guidance
    assert "paid, non-reproducible build inputs" in guidance
    assert "survive reboots" in guidance
    assert "/tmp/hoi4-agent-scripts/" in guidance
    for path in GEMINI_GUIDANCE_FILES:
        assert "/tmp/hoi4-agent-assets" not in path.read_text(encoding="utf-8")
