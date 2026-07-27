"""Shared deterministic property-test profiles."""

from __future__ import annotations

import os

from hypothesis import HealthCheck, settings

settings.register_profile(
    "ci",
    max_examples=200,
    deadline=None,
    derandomize=True,
    suppress_health_check=(HealthCheck.too_slow,),
)
settings.register_profile(
    "weekly",
    max_examples=2_000,
    deadline=None,
    derandomize=True,
    suppress_health_check=(HealthCheck.too_slow,),
)
settings.load_profile(os.environ.get("HOI4_HYPOTHESIS_PROFILE", "ci"))
