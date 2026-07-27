from __future__ import annotations

import json
from pathlib import Path

import pytest

import hoi4.compatibility_audit as audit
from hoi4.effects_catalog import TECHNOLOGY_CATEGORIES


def _write_fake_install(root: Path, *, omit_technology: str | None = None) -> None:
    documentation = root / "documentation"
    documentation.mkdir(parents=True)
    effect_entries = "\n".join(f"## {key}" for key in sorted(audit.catalog_effect_keys()))
    modifier_entries = "\n".join(f"## {key}" for key in sorted(audit.catalog_modifier_keys()))
    (documentation / "effects_documentation.md").write_text(effect_entries, encoding="utf-8")
    (documentation / "modifiers_documentation.md").write_text(
        modifier_entries,
        encoding="utf-8",
    )
    tags = root / "common" / "technology_tags"
    tags.mkdir(parents=True)
    categories = [key for key in TECHNOLOGY_CATEGORIES if key != omit_technology]
    (tags / "00_technology.txt").write_text(
        "technology_categories = {\n" + "\n".join(categories) + "\n}\n",
        encoding="utf-8",
    )


def test_catalogs_match_exact_generated_game_entries(tmp_path: Path) -> None:
    _write_fake_install(tmp_path)

    assert audit.catalog_findings(tmp_path) == ()


def test_stale_technology_category_blocks_audit(tmp_path: Path) -> None:
    missing = TECHNOLOGY_CATEGORIES[0]
    _write_fake_install(tmp_path, omit_technology=missing)

    findings = audit.catalog_findings(tmp_path)

    assert any(
        finding.code == "stale_technology_category" and missing in finding.message
        for finding in findings
    )


def test_missing_sdk_technology_category_blocks_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = TECHNOLOGY_CATEGORIES[0]
    _write_fake_install(tmp_path)
    monkeypatch.setattr(
        audit,
        "TECHNOLOGY_CATEGORIES",
        tuple(key for key in TECHNOLOGY_CATEGORIES if key != missing),
    )

    findings = audit.catalog_findings(tmp_path)

    assert any(
        finding.code == "missing_technology_category" and missing in finding.message
        for finding in findings
    )


def test_invalid_modifier_catalog_entry_blocks_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fake_install(tmp_path)
    monkeypatch.setattr(
        audit,
        "MODIFIER_CATEGORIES",
        [("Invalid", [("Invalid", "definitely_not_a_modifier", "1")])],
    )

    findings = audit.catalog_findings(tmp_path)

    assert findings == (
        audit.AuditFinding(
            "unknown_modifier_catalog_entry",
            "Modifier is absent from game docs: definitely_not_a_modifier",
        ),
    )


def test_changed_corpus_byte_blocks_fingerprinted_audit(tmp_path: Path) -> None:
    corpus = tmp_path / "Empire"
    corpus.mkdir()
    file = corpus / "common.txt"
    file.write_bytes(b"verified")
    count, digest = audit.tree_fingerprint(corpus)
    manifest = tmp_path / "Empire.sha256.json"
    manifest.write_text(
        json.dumps({"algorithm": "sha256-tree-v1", "file_count": count, "digest": digest}),
        encoding="utf-8",
    )
    assert audit.fingerprint_findings(corpus, manifest) == ()

    file.write_bytes(b"changed")

    findings = audit.fingerprint_findings(corpus, manifest)
    assert findings[0].code == "audit_corpus_changed"
