"""Shared deterministic property-test profiles."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any

import pytest
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


@pytest.fixture
def patch_mod_helper(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any, Any], None]:
    """Replace a module-level helper in every ``hoi4.mod`` layer module that binds it.

    The ``Mod`` layers each import the helpers they use at module level, so a
    test that counts calls must swap the helper in all of them. Pass the
    original function: only names bound to that exact object are replaced.

    A helper imported inside a function body is looked up at call time in its
    home module and is not reached by this fixture; patch the home module for
    those (for example ``hoi4.parser.parse_pdx``).
    """

    import hoi4.mod  # noqa: F401  (loads every layer module)

    def patch(original: Any, replacement: Any) -> None:
        name = original.__name__
        targets = [
            module
            for module_name, module in sys.modules.items()
            if module_name.startswith("hoi4.mod._") and getattr(module, name, None) is original
        ]
        assert targets, f"no hoi4.mod layer module binds {name}"
        for module in targets:
            monkeypatch.setattr(module, name, replacement)

    return patch
