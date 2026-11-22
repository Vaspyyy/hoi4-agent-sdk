#!/usr/bin/env python3
"""Find uses of names that the 0.7.0 API cleanup removed or renamed.

Usage (from the repository root)::

    python scripts/check_removed_api_names.py [paths...]

The default paths are ``src tests docs examples README.md AGENTS.md CLAUDE.md``.
Python files are parsed whole; Markdown files contribute their ```python
blocks to the code checks, and all of their text to the prose checks.

It reports:

* calls and mentions of removed ``Mod`` methods (``mod.upsert_focus(...)``,
  ``Mod.effect_add_political_power(...)``) and of the old names of renamed
  ones (``set_idea_path``, ``content_source``, ...);
* keyword arguments that were renamed or dropped, on the methods that had
  them (``create_oob(..., country_tag=...)``, ``validate(suppress_warnings=...)``);
* removed module functions and top-level names (``hoi4.effect_block``,
  ``from hoi4 import scope_block``, ``hoi4.ValidationError``, ...).

A call inside ``with pytest.raises(...)`` is a test that the old name fails,
so it is not reported. CHANGELOG.md is not scanned: its migration table names
every old call on purpose. Exits with status 1 when anything is found.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import sys
from collections.abc import Iterator
from pathlib import Path

DEFAULT_PATHS = ["src", "tests", "docs", "examples", "README.md", "AGENTS.md", "CLAUDE.md"]

EFFECT_BUILDERS = [
    "add_air_experience", "add_army_experience", "add_bunker", "add_civilian_factory",
    "add_dynamic_modifier", "add_equipment", "add_infrastructure", "add_manpower",
    "add_military_factory", "add_navy_experience", "add_political_power", "add_stability",
    "add_state_building", "add_state_core", "add_target_to_faction", "add_tech_bonus",
    "add_to_faction", "add_war_support", "convert_existing_or_spawn_revolt",
    "convert_puppet_to_ally", "create_faction", "create_unit", "create_wargoal",
    "declare_war", "declare_war_from", "division_template", "end_puppet",
    "force_update_dynamic_modifier", "join_faction", "load_focus_tree", "release",
    "release_puppet", "remove_dynamic_modifier", "remove_state_core",
    "schedule_country_event", "set_autonomy", "set_politics", "set_rule",
    "set_technologies", "set_technology", "spawn_civil_war_with_focus_tree",
    "spawn_revolution", "start_civil_war", "swap_idea", "transfer_state",
    "transfer_state_with_core", "upgrade_idea_chain", "white_peace",
]

# Mod methods that no longer exist (removed, or the old name of a renamed one).
REMOVED_MOD_METHODS = {
    "declare_war", "create_wargoal", "start_civil_war", "effect_block",
    "scope_block", "ruling_parties", "leader_ideologies_for_party",
    "default_leader_ideology", "suggest_tag", "suggest_focus_icon", "suggest_idea_icon",
    "create_scripted_trigger", "create_scripted_effect", "create_country_leader_trait",
    "create_unit_leader_trait", "create_technology", "create_sub_unit", "upsert_focus",
    "set_state_properties", "patch_state_history", "batch_set_owner", "set_idea_path",
    "ensure_country_states_in_mod", "content_source",
    *(f"effect_{name}" for name in EFFECT_BUILDERS),
}
# Receivers on which a removed Mod method name is a valid, different function.
ALLOWED_RECEIVERS = {
    "declare_war": {"effects"},
    "create_wargoal": {"effects"},
    "start_civil_war": {"effects"},
    "scope_block": {"effects", "builders"},
}

# Removed or renamed keyword arguments, per method.
RENAMED_KEYWORDS = {
    "create_focus_tree": {"country_tag"},
    "ensure_focus_tree": {"country_tag"},
    "create_oob": {"country_tag"},
    "create_equipment_variant": {"country_tag"},
    "set_loc": {"file_path", "replace"},
    "set_focus_loc": {"file_path"},
    "create_state": {"localisation_file"},
    "create_strategic_region": {"localisation_file"},
    "create_country": {"allow_vanilla_override"},
    "import_flag_to_mod": {"vanilla_override"},
    "validate": {"suppress_warnings"},
    "validate_effect": {"suppress_warnings"},
    "create_idea": {"desc"},
    "set_politics": {"elections_frequency"},
}

# Module-level functions that were removed.
REMOVED_FUNCTIONS = {
    "load_bookmarks", "write_decisions_file", "write_focus_tree", "load_ideologies",
    "delete_localization_keys", "write_on_actions_file", "ensure_state_in_mod",
    "patch_state_history_owner_cores", "load_all_tags", "collect_character_ids",
}
# Names that left ``hoi4`` (and ``hoi4.script``) itself.
REMOVED_TOP_LEVEL = {
    "effect_block", "scope_block", "import_flag_to_mod", "import_portrait_to_mod",
    "write_portrait_gfx", "version", "PackageNotFoundError", "ValidationError",
}
REMOVED_FROM_SCRIPT = {"effect_block", "scope_block"}

PROSE = [
    (re.compile(r"\b(?:mod|Mod|self)\.(" + "|".join(sorted(REMOVED_MOD_METHODS)) + r")\b"),
     "removed Mod method"),
    (re.compile(r"\bhoi4\.(effect_block|scope_block|import_flag_to_mod|import_portrait_to_mod|"
                r"write_portrait_gfx|version|PackageNotFoundError|ValidationError)\b"),
     "name removed from hoi4"),
    (re.compile(r"\bhoi4\.script\.(effect_block|scope_block)\b"), "name removed from hoi4.script"),
    (re.compile(r"\bValidationError\b"), "renamed to ValidationIssue"),
    (re.compile(r"\b(" + "|".join(sorted(REMOVED_FUNCTIONS)) + r")\b"), "removed module function"),
]


def sources(paths: list[Path]) -> Iterator[tuple[Path, int, str, bool]]:
    """Yield ``(file, first line, text, is_code)`` for every scanned piece."""
    for base in paths:
        if not base.exists():
            continue
        files = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in files:
            if path.name == "CHANGELOG.md" or path.resolve() == Path(__file__).resolve():
                continue
            if path.suffix == ".py":
                text = path.read_text(encoding="utf-8")
                yield path, 1, text, True
                for line, prose in python_prose(text):
                    yield path, line, prose, False
            elif path.suffix == ".md":
                text = path.read_text(encoding="utf-8")
                yield path, 1, text, False
                for block in re.finditer(r"```python\n(.*?)```", text, re.S):
                    yield path, text[: block.start()].count("\n") + 2, block.group(1), True


def python_prose(code: str) -> Iterator[tuple[int, str]]:
    """Yield the docstrings and comments of a Python file with their first line.

    Other string literals are data (a test may list removed names to check
    that they are gone), so the prose checks skip them.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    yield body[0].lineno, body[0].value.value
    for token in tokenize.generate_tokens(io.StringIO(code).readline):
        if token.type == tokenize.COMMENT:
            yield token.start[0], token.string


def receiver_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def code_problems(code: str) -> Iterator[tuple[int, str]]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return
    expected: set[int] = set()
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        if isinstance(node, ast.With) and any(
            "raises" in ast.unparse(item.context_expr) for item in node.items
        ):
            expected.update(id(inner) for inner in ast.walk(node))
    for node in ast.walk(tree):
        if id(node) in expected:
            continue
        if isinstance(node, ast.ImportFrom) and node.module in ("hoi4", "hoi4.script"):
            removed = REMOVED_TOP_LEVEL if node.module == "hoi4" else REMOVED_FROM_SCRIPT
            for alias in node.names:
                if alias.name in removed or alias.name in REMOVED_FUNCTIONS:
                    yield node.lineno, f"from {node.module} import {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("hoi4."):
            for alias in node.names:
                if alias.name in REMOVED_FUNCTIONS:
                    yield node.lineno, f"from {node.module} import {alias.name}"
        elif isinstance(node, ast.Attribute):
            receiver = receiver_name(node.value)
            if node.attr in REMOVED_MOD_METHODS and receiver not in ALLOWED_RECEIVERS.get(node.attr, ()):
                yield node.lineno, f"{receiver}.{node.attr}"
            elif node.attr in REMOVED_FUNCTIONS:
                yield node.lineno, f"{receiver}.{node.attr}"
            elif receiver == "hoi4" and node.attr in REMOVED_TOP_LEVEL:
                yield node.lineno, f"hoi4.{node.attr}"
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id not in defined and (
                func.id in REMOVED_FUNCTIONS or func.id in ("effect_block", "scope_block")
            ):
                yield node.lineno, f"{func.id}(...)"
            name = func.attr if isinstance(func, ast.Attribute) else ""
            for keyword in node.keywords:
                if keyword.arg in RENAMED_KEYWORDS.get(name, ()):
                    yield node.lineno, f"{name}({keyword.arg}=...)"


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in (argv or DEFAULT_PATHS)]
    found = 0
    for path, offset, text, is_code in sources(paths):
        if is_code:
            hits = [(offset + line - 1, what) for line, what in code_problems(text)]
        else:
            hits = []
            for pattern, why in PROSE:
                for match in pattern.finditer(text):
                    line = text[: match.start()].count("\n") + 1
                    hits.append((line, f"{match.group(0)} ({why})"))
        for line, what in sorted(set(hits)):
            print(f"{path}:{line}: {what}")
            found += 1
    print(f"{found} use(s) of removed or renamed names", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
