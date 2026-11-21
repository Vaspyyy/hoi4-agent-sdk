"""Every finding the SDK can produce has a registered, documented code.

The test reads the Python source of ``src/hoi4`` through its syntax tree
(``ast``) and looks at every ``ValidationError(...)`` call. The ``code`` and
``severity`` arguments are followed back to string constants:

* a constant is used as it is;
* ``a if cond else b`` gives both branches;
* a local variable gives every value assigned to it in the function;
* ``table.get(key, default)`` on a dict literal gives its values and default;
* a parameter of the enclosing function is followed to every call of that
  function in the package (by keyword, by position, or its default);
* an f-string must match at least one registered code.

A call whose code cannot be followed to constants fails the test, so a new
helper has to keep its codes visible to it. The severity a call passes must
be one of ``SEVERITIES``; ``Mod.validate()`` then gives every finding the
severity its code is registered with.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from hoi4.validation import (
    SEVERITIES,
    VALIDATION_CODES,
    VALIDATION_ERROR_CODES,
    VALIDATION_INFO_CODES,
    VALIDATION_SEVERITIES,
    VALIDATION_WARNING_CODES,
)

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "hoi4"
CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass
class _Module:
    path: Path
    tree: ast.Module
    parent_function: dict[int, ast.FunctionDef | ast.AsyncFunctionDef]


def _modules() -> list[_Module]:
    modules = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents: dict[int, ast.FunctionDef | ast.AsyncFunctionDef] = {}

        def visit(node: ast.AST, function: ast.FunctionDef | ast.AsyncFunctionDef | None) -> None:
            for child in ast.iter_child_nodes(node):
                if function is not None:
                    parents[id(child)] = function
                inner = (
                    child
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    else function
                )
                visit(child, inner)

        visit(tree, None)
        modules.append(_Module(path, tree, parents))
    return modules


MODULES = _modules()


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


class _Unresolved(Exception):
    pass


def _parameters(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    args = function.args
    return [*args.posonlyargs, *args.args, *args.kwonlyargs]


def _default(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> ast.expr | None:
    args = function.args
    positional = [*args.posonlyargs, *args.args]
    offset = len(positional) - len(args.defaults)
    for index, arg in enumerate(positional):
        if arg.arg == name and index >= offset:
            return args.defaults[index - offset]
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        if arg.arg == name:
            return default
    return None


def _call_sites(
    function: ast.FunctionDef | ast.AsyncFunctionDef, module: _Module
) -> list[tuple[ast.Call, _Module]]:
    """Return every call of ``function``.

    A function nested in another one can only be called from there, so only
    that body is searched; a method or module-level function is looked up by
    name in the whole package.
    """

    enclosing = module.parent_function.get(id(function))
    if enclosing is not None:
        return [
            (node, module)
            for node in ast.walk(enclosing)
            if isinstance(node, ast.Call) and _call_name(node) == function.name
        ]
    sites = []
    for other in MODULES:
        for node in ast.walk(other.tree):
            if isinstance(node, ast.Call) and _call_name(node) == function.name:
                sites.append((node, other))
    return sites


def _argument(
    call: ast.Call,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    positional = [arg.arg for arg in [*function.args.posonlyargs, *function.args.args]]
    if positional and positional[0] in {"self", "cls"} and isinstance(call.func, ast.Attribute):
        positional = positional[1:]
    if name in positional:
        index = positional.index(name)
        if index < len(call.args) and not any(
            isinstance(arg, ast.Starred) for arg in call.args[: index + 1]
        ):
            return call.args[index]
    return _default(function, name)


def _is_parameter(expr: ast.expr | None, module: _Module, node: ast.AST) -> bool:
    function = module.parent_function.get(id(node))
    return (
        function is not None
        and isinstance(expr, ast.Name)
        and expr.id in {arg.arg for arg in _parameters(function)}
    )


def _resolve(
    expr: ast.expr | None,
    module: _Module,
    node: ast.AST,
    *,
    missing: str | None,
    depth: int = 0,
) -> set[str]:
    """Return every string ``expr`` can evaluate to at ``node``."""

    return {
        values[0]
        for values in _resolve_together((expr,), (missing,), module, node, depth=depth)
    }


_Item = ast.expr | frozenset[str] | None


def _resolve_together(
    items: tuple[_Item, ...],
    missing: tuple[str | None, ...],
    module: _Module,
    node: ast.AST,
    *,
    depth: int = 0,
) -> set[tuple[str, ...]]:
    """Return every combination of strings ``items`` can take together.

    An item is an expression at ``node`` or a set of strings already found.
    Expressions that depend on the same thing are followed together, so the
    result holds the real combinations and not every mix:

    * ``a if cond else b`` items with the same ``cond`` take the same branch;
    * parameters of the enclosing function are replaced, at each call of that
      function, by the arguments of that one call (any number of levels).
    """

    if depth > 12:
        raise _Unresolved("too deep")
    # Same condition, same branch.
    for item in items:
        if isinstance(item, ast.IfExp):
            test = ast.dump(item.test)

            def branch(chosen: str) -> tuple[_Item, ...]:
                return tuple(
                    getattr(other, chosen)
                    if isinstance(other, ast.IfExp) and ast.dump(other.test) == test
                    else other
                    for other in items
                )

            return _resolve_together(
                branch("body"), missing, module, node, depth=depth + 1
            ) | _resolve_together(branch("orelse"), missing, module, node, depth=depth + 1)
    # Everything that is not a parameter is resolved where it stands.
    resolved: list[_Item] = []
    for item, default in zip(items, missing):
        if item is None or isinstance(item, frozenset):
            resolved.append(item)
        elif _is_parameter(item, module, node):
            resolved.append(item)
        else:
            resolved.append(frozenset(_resolve_one(item, module, node, default, depth)))
    if any(isinstance(item, ast.expr) for item in resolved):
        function = module.parent_function[id(node)]
        sites = _call_sites(function, module)
        if not sites:
            raise _Unresolved(f"{function.name}() is never called")
        result: set[tuple[str, ...]] = set()
        for call, caller in sites:
            at_site = tuple(
                _argument(call, function, item.id) if isinstance(item, ast.Name) else item
                for item in resolved
            )
            result |= _resolve_together(at_site, missing, caller, call, depth=depth + 1)
        return result
    combinations: set[tuple[str, ...]] = {()}
    for item, default in zip(resolved, missing):
        if item is None:
            if default is None:
                raise _Unresolved("argument not given")
            values: frozenset[str] = frozenset({default})
        else:
            assert isinstance(item, frozenset)
            values = item
        combinations = {(*combination, value) for combination in combinations for value in values}
    return combinations


def _resolve_one(
    expr: ast.expr, module: _Module, node: ast.AST, missing: str | None, depth: int
) -> set[str]:
    """Return the strings an expression that is not a parameter can take."""

    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return {expr.value}
    if isinstance(expr, ast.JoinedStr):
        pattern = "".join(
            re.escape(str(part.value)) if isinstance(part, ast.Constant) else "[a-z0-9_]+"
            for part in expr.values
        )
        matches = {code for code in VALIDATION_CODES if re.fullmatch(pattern, code)}
        if not matches:
            raise _Unresolved(f"f-string {ast.unparse(expr)} matches no registered code")
        return matches
    function = module.parent_function.get(id(node))
    if isinstance(expr, ast.Call) and _call_name(expr) == "get" and isinstance(
        expr.func, ast.Attribute
    ):
        table = expr.func.value
        if isinstance(table, ast.Name) and function is not None:
            values: set[str] = set()
            for sub in ast.walk(function):
                if (
                    isinstance(sub, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == table.id for t in sub.targets)
                    and isinstance(sub.value, ast.Dict)
                ):
                    for value in sub.value.values:
                        values |= _resolve(value, module, sub, missing=None, depth=depth + 1)
            if values and len(expr.args) == 2:
                return values | _resolve(expr.args[1], module, node, missing=None, depth=depth + 1)
        raise _Unresolved(f"cannot follow {ast.unparse(expr)}")
    if isinstance(expr, ast.Name) and function is not None:
        assigned: set[str] = set()
        for sub in ast.walk(function):
            if isinstance(sub, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == expr.id for target in sub.targets
            ):
                assigned |= _resolve(sub.value, module, sub, missing=None, depth=depth + 1)
        if assigned:
            return assigned
    raise _Unresolved(f"cannot follow {ast.unparse(expr)}")


def _pairs(call: ast.Call, module: _Module) -> set[tuple[str, str]]:
    """Return every ``(code, severity)`` pair a ``ValidationError`` call can build.

    ``code`` and ``severity`` are followed together (see ``_resolve_together``),
    so a helper called once with an error code and once with a warning code
    gives two pairs, not four.
    """

    keywords = {keyword.arg: keyword.value for keyword in call.keywords}
    code_expr = keywords.get("code")
    if code_expr is None:
        raise _Unresolved("no code")
    found = _resolve_together(
        (code_expr, keywords.get("severity")), (None, "error"), module, call
    )
    return {(code, severity) for code, severity in found}


def _constructions() -> list[tuple[ast.Call, _Module]]:
    return [
        (node, module)
        for module in MODULES
        for node in ast.walk(module.tree)
        if isinstance(node, ast.Call) and _call_name(node) == "ValidationError"
    ]


def test_every_validation_error_has_a_registered_code():
    problems: list[str] = []
    constructions = _constructions()
    assert len(constructions) > 100
    for call, module in constructions:
        where = f"{module.path.relative_to(PACKAGE.parent)}:{call.lineno}"
        try:
            pairs = _pairs(call, module)
        except _Unresolved as error:
            problems.append(f"{where}: code cannot be followed ({error})")
            continue
        for code, severity in sorted(pairs):
            if not code:
                problems.append(f"{where}: empty code")
            elif code not in VALIDATION_CODES:
                problems.append(f"{where}: code {code!r} is not registered")
            if severity not in SEVERITIES:
                problems.append(f"{where}: unknown severity {severity!r}")
    assert not problems, "\n".join(problems)


def test_every_raise_site_uses_the_registered_severity():
    """A finding has the same severity whether or not ``Mod.validate()`` ran."""

    problems: list[str] = []
    for call, module in _constructions():
        where = f"{module.path.relative_to(PACKAGE.parent)}:{call.lineno}"
        try:
            pairs = _pairs(call, module)
        except _Unresolved:
            continue  # reported by the test above
        for code, severity in sorted(pairs):
            expected = VALIDATION_SEVERITIES.get(code)
            if expected is not None and severity != expected:
                problems.append(
                    f"{where}: {code!r} is raised as {severity!r}, registered as {expected!r}"
                )
    assert not problems, "\n".join(problems)


def test_registry_tables_partition_the_codes():
    assert all(CODE_RE.match(code) for code in VALIDATION_CODES)
    assert all(description.strip() for description in VALIDATION_CODES.values())
    assert set(VALIDATION_SEVERITIES) == set(VALIDATION_CODES)
    tables = (VALIDATION_ERROR_CODES, VALIDATION_WARNING_CODES, VALIDATION_INFO_CODES)
    assert sum(len(table) for table in tables) == len(VALIDATION_CODES)
    assert set().union(*tables) == set(VALIDATION_CODES)


def test_every_registered_code_is_documented():
    text = (PACKAGE.parents[1] / "docs" / "validation.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z0-9_]+)` \| (error|warning|info) \|", text, re.M))
    expected = set(VALIDATION_SEVERITIES.items())
    assert not expected - documented, sorted(expected - documented)
    assert not documented - expected, sorted(documented - expected)
