"""Validation-stage names shared by the public SDK and release gate."""

from typing import Literal

ValidationStage = Literal["build", "package", "release"]
VALIDATION_STAGES: tuple[ValidationStage, ...] = ("build", "package", "release")


def require_validation_stage(value: str) -> ValidationStage:
    if value not in VALIDATION_STAGES:
        choices = ", ".join(VALIDATION_STAGES)
        raise ValueError(f"Unknown validation stage '{value}'. Expected one of: {choices}")
    return value  # type: ignore[return-value]
