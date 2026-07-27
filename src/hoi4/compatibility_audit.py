"""Read-only compatibility audit against an installed HOI4 release."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .effects_catalog import EFFECT_CATEGORIES, TECHNOLOGY_CATEGORIES
from .modifiers_catalog import MODIFIER_CATEGORIES
from .parser import PdxNode, parse_pdx
from .release_gate import DiffBudget, GateReport, run_release_gate

EMPIRE_REQUIRED_PROBES: tuple[str, ...] = (
    "focus",
    "event",
    "decision",
    "idea",
    "on_action",
    "localization",
    "country",
    "state",
    "ideology",
    "bookmark",
)


@dataclass(frozen=True)
class AuditFinding:
    """One compatibility mismatch discovered by the audit."""

    code: str
    message: str


@dataclass(frozen=True)
class CompatibilityAuditReport:
    """Complete installed-game compatibility report."""

    game_version: str
    mod_root: Path
    hoi4_install: Path
    findings: tuple[AuditFinding, ...]
    gate: GateReport

    @property
    def success(self) -> bool:
        return not self.findings and self.gate.success

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "game_version": self.game_version,
            "mod_root": str(self.mod_root),
            "hoi4_install": str(self.hoi4_install),
            "findings": [
                {"code": finding.code, "message": finding.message}
                for finding in self.findings
            ],
            "release_gate": self.gate.to_dict(),
        }


def read_game_version(hoi4_install: str | Path) -> str:
    """Read the installed version recorded by the launcher."""

    path = Path(hoi4_install) / "launcher-settings.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read HOI4 version from {path}: {error}") from error
    for key in ("rawVersion", "version"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"HOI4 launcher settings do not contain a version: {path}")


def documentation_entries(path: str | Path) -> frozenset[str]:
    """Extract exact level-two entries from generated HOI4 Markdown docs."""

    text = Path(path).read_text(encoding="utf-8-sig")
    return frozenset(
        heading.strip()
        for heading in re.findall(r"^## ([^\r\n]+)$", text, flags=re.MULTILINE)
        if heading != "Table of Content"
        and not heading.startswith(("Effects for scope ", "Modifiers for scope "))
    )


def _first_effect_key(node: PdxNode) -> str | None:
    children = [child for child in node.children if not child.is_comment]
    if not children:
        return None
    first = children[0]
    if first.key is None:
        return None
    if first.key.isdigit() or re.fullmatch(r"[A-Z][A-Z0-9_]{1,4}", first.key):
        return _first_effect_key(first)
    return first.key


def catalog_effect_keys() -> frozenset[str]:
    """Return the executable effect names exposed by the SDK catalog."""

    keys: set[str] = set()
    for _category, effects in EFFECT_CATEGORIES:
        for _label, script in effects:
            key = _first_effect_key(parse_pdx(script))
            if key is not None:
                keys.add(key)
    return frozenset(keys)


def catalog_modifier_keys() -> frozenset[str]:
    """Return modifier names exposed by the SDK catalog."""

    return frozenset(
        key
        for _category, modifiers in MODIFIER_CATEGORIES
        for _label, key, _default in modifiers
    )


def installed_technology_categories(hoi4_install: str | Path) -> frozenset[str]:
    """Extract technology categories from the installed game's canonical file."""

    path = Path(hoi4_install) / "common" / "technology_tags" / "00_technology.txt"
    try:
        root = parse_pdx(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot parse technology categories from {path}: {error}") from error
    block = root.get_block("technology_categories")
    if block is None:
        raise ValueError(f"technology_categories block is missing from {path}")
    return frozenset(
        child.value
        for child in block.children
        if not child.is_comment and child.is_bare() and child.value
    )


def catalog_findings(hoi4_install: str | Path) -> tuple[AuditFinding, ...]:
    """Compare SDK catalogs with the generated docs and installed technologies."""

    install = Path(hoi4_install)
    documentation = install / "documentation"
    effect_docs = documentation_entries(documentation / "effects_documentation.md")
    modifier_docs = documentation_entries(documentation / "modifiers_documentation.md")
    installed_categories = installed_technology_categories(install)

    findings: list[AuditFinding] = []
    for key in sorted(catalog_effect_keys() - effect_docs):
        findings.append(
            AuditFinding("unknown_effect_catalog_entry", f"Effect is absent from game docs: {key}")
        )
    for key in sorted(catalog_modifier_keys() - modifier_docs):
        findings.append(
            AuditFinding(
                "unknown_modifier_catalog_entry",
                f"Modifier is absent from game docs: {key}",
            )
        )

    sdk_categories = frozenset(TECHNOLOGY_CATEGORIES)
    for key in sorted(installed_categories - sdk_categories):
        findings.append(
            AuditFinding(
                "missing_technology_category",
                f"Installed technology category is missing from SDK catalog: {key}",
            )
        )
    for key in sorted(sdk_categories - installed_categories):
        findings.append(
            AuditFinding(
                "stale_technology_category",
                f"SDK technology category is absent from installed game: {key}",
            )
        )
    return tuple(findings)


def tree_fingerprint(root: str | Path) -> tuple[int, str]:
    """Return a stable digest over relative paths and file bytes."""

    directory = Path(root)
    digest = hashlib.sha256()
    count = 0
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        count += 1
    return count, digest.hexdigest()


def fingerprint_findings(
    mod_root: str | Path,
    fingerprint_manifest: str | Path,
) -> tuple[AuditFinding, ...]:
    """Compare a corpus against its private trusted fingerprint."""

    manifest_path = Path(fingerprint_manifest)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload["algorithm"] != "sha256-tree-v1":
            raise ValueError(f"unsupported algorithm {payload['algorithm']!r}")
        expected_count = int(payload["file_count"])
        expected_digest = str(payload["digest"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return (
            AuditFinding(
                "invalid_corpus_fingerprint",
                f"Cannot read trusted corpus fingerprint {manifest_path}: {error}",
            ),
        )
    count, digest = tree_fingerprint(mod_root)
    if (count, digest) == (expected_count, expected_digest):
        return ()
    return (
        AuditFinding(
            "audit_corpus_changed",
            (
                "Audit corpus does not match its trusted fingerprint: "
                f"expected {expected_count} files/{expected_digest}, "
                f"found {count} files/{digest}"
            ),
        ),
    )


def run_compatibility_audit(
    mod_root: str | Path,
    *,
    hoi4_install: str | Path,
    required_probes: Iterable[str] = EMPIRE_REQUIRED_PROBES,
    budget: DiffBudget | None = None,
    fingerprint_manifest: str | Path | None = None,
) -> CompatibilityAuditReport:
    """Run catalogs, strict validation, source-churn, and read-only checks."""

    root = Path(mod_root).resolve()
    install = Path(hoi4_install).resolve()
    normalized_required = tuple(dict.fromkeys(required_probes))
    findings = list(catalog_findings(install))
    if fingerprint_manifest is not None:
        findings.extend(fingerprint_findings(root, fingerprint_manifest))
    gate = run_release_gate(
        root,
        hoi4_install=install,
        budget=budget,
        validate_icons=True,
        strict_localization=True,
        strict_loading=True,
        required_probes=normalized_required,
        min_probes=len(normalized_required),
    )
    return CompatibilityAuditReport(
        game_version=read_game_version(install),
        mod_root=root,
        hoi4_install=install,
        findings=tuple(findings),
        gate=gate,
    )


def format_compatibility_report(report: CompatibilityAuditReport) -> str:
    """Format a concise human-readable compatibility report."""

    lines = [
        f"HOI4 compatibility audit: {'PASS' if report.success else 'FAIL'}",
        f"game version: {report.game_version}",
        f"mod: {report.mod_root}",
        f"hoi4 install: {report.hoi4_install}",
        f"compatibility findings: {len(report.findings)}",
    ]
    lines.extend(f"  {finding.code}: {finding.message}" for finding in report.findings)
    lines.extend(
        (
            f"release gate: {'PASS' if report.gate.success else 'FAIL'}",
            (
                "required probes: "
                f"{len(report.gate.required_probes) - len(report.gate.missing_required_probes)}"
                f"/{len(report.gate.required_probes)} passed"
            ),
            f"filesystem changes: {len(report.gate.filesystem_changes)}",
        )
    )
    return "\n".join(lines)
