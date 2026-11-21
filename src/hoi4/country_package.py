"""Structured completeness reports for SDK-authored countries."""

from __future__ import annotations

from dataclasses import dataclass

from .types import ValidationError
from .validation import COUNTRY_PACKAGE_REQUIREMENT_CODES


@dataclass(frozen=True)
class CountryPackageReport:
    """Static package completeness; not a proof of dynamic trigger reachability."""

    tag: str
    findings: tuple[ValidationError, ...] = ()
    lifecycle: str = "starting"
    advisor_count: int = 0
    commander_count: int = 0
    character_count: int = 0
    owned_state_count: int = 0
    runtime_state_ids: tuple[int, ...] = ()
    activation_sources: tuple[str, ...] = ()
    analysis_scope: str = "structural"
    proves_dynamic_achievability: bool = False

    @property
    def errors(self) -> tuple[ValidationError, ...]:
        return tuple(
            finding for finding in self.findings if finding.severity == "error"
        )

    @property
    def warnings(self) -> tuple[ValidationError, ...]:
        return tuple(
            finding for finding in self.findings if finding.severity == "warning"
        )

    @property
    def unmet_requirements(self) -> tuple[ValidationError, ...]:
        """Findings about what the SDK asks of a complete country.

        The game accepts a country without them (they are warnings), but the
        package is not complete while one is open.
        """

        return tuple(
            finding
            for finding in self.findings
            if finding.code in COUNTRY_PACKAGE_REQUIREMENT_CODES
        )

    @property
    def complete(self) -> bool:
        """Whether every structural package requirement passes."""

        return not self.errors and not self.unmet_requirements

    @property
    def structurally_complete(self) -> bool:
        return self.complete

    def to_dict(self) -> dict[str, object]:
        return {
            "tag": self.tag,
            "complete": self.complete,
            "structurally_complete": self.structurally_complete,
            "analysis_scope": self.analysis_scope,
            "proves_dynamic_achievability": self.proves_dynamic_achievability,
            "lifecycle": self.lifecycle,
            "advisor_count": self.advisor_count,
            "commander_count": self.commander_count,
            "character_count": self.character_count,
            "owned_state_count": self.owned_state_count,
            "runtime_state_ids": list(self.runtime_state_ids),
            "activation_sources": list(self.activation_sources),
            "findings": [
                {
                    "severity": finding.severity,
                    "code": finding.code,
                    "message": finding.message,
                    "file_path": finding.file_path,
                    "state_id": finding.state_id,
                }
                for finding in self.findings
            ],
        }
