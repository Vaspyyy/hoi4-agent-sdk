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
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[ast.Call, _Module]]:
    sites = []
    for module in MODULES:
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Call) and _call_name(node) == function.name:
                sites.append((node, module))
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


def _resolve(
    expr: ast.expr | None,
    module: _Module,
    node: ast.AST,
    *,
    missing: str | None,
    depth: int = 0,
) -> set[str]:
    """Return every string ``expr`` can evaluate to at ``node``."""

    if depth > 8:
        raise _Unresolved("too deep")
    if expr is None:
        if missing is None:
            raise _Unresolved("argument not given")
        return {missing}
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return {expr.value}
    if isinstance(expr, ast.IfExp):
        return _resolve(expr.body, module, node, missing=missing, depth=depth + 1) | _resolve(
            expr.orelse, module, node, missing=missing, depth=depth + 1
        )
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
        if expr.id in {arg.arg for arg in _parameters(function)}:
            values = set()
            sites = _call_sites(function)
            if not sites:
                raise _Unresolved(f"{function.name}() is never called")
            for call, caller in sites:
                argument = _argument(call, function, expr.id)
                values |= _resolve(argument, caller, call, missing=missing, depth=depth + 1)
            return values
        assigned: set[str] = set()
        for sub in ast.walk(function):
            if isinstance(sub, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == expr.id for target in sub.targets
            ):
                assigned |= _resolve(sub.value, module, sub, missing=None, depth=depth + 1)
        if assigned:
            return assigned
    raise _Unresolved(f"cannot follow {ast.unparse(expr)}")


def _pairs(
    call: ast.Call, module: _Module
) -> list[tuple[set[str], set[str], str]]:
    """Return ``(codes, severities, where)`` for each concrete call path.

    When ``code`` and ``severity`` are parameters of the same helper, they are
    followed together through each call of that helper, so a helper called
    once with an error code and once with a warning code is checked per call.
    """

    keywords = {keyword.arg: keyword.value for keyword in call.keywords}
    code_expr = keywords.get("code")
    severity_expr = keywords.get("severity")
    where = f"{module.path.relative_to(PACKAGE.parent)}:{call.lineno}"
    function = module.parent_function.get(id(call))
    parameters = {arg.arg for arg in _parameters(function)} if function else set()

    def is_param(expr: ast.expr | None) -> bool:
        return isinstance(expr, ast.Name) and expr.id in parameters

    if function is not None and is_param(code_expr) and is_param(severity_expr):
        assert isinstance(code_expr, ast.Name) and isinstance(severity_expr, ast.Name)
        result = []
        for site, caller in _call_sites(function):
            codes = _resolve(
                _argument(site, function, code_expr.id), caller, site, missing=None
            )
            severities = _resolve(
                _argument(site, function, severity_expr.id), caller, site, missing="error"
            )
            result.append((codes, severities, f"{where} via {caller.path.name}:{site.lineno}"))
        return result
    if code_expr is None:
        raise _Unresolved("no code")
    codes = _resolve(code_expr, module, call, missing=None)
    severities = _resolve(severity_expr, module, call, missing="error")
    return [(codes, severities, where)]


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
        for codes, severities, path in pairs:
            for code in sorted(codes):
                if not code:
                    problems.append(f"{path}: empty code")
                elif code not in VALIDATION_CODES:
                    problems.append(f"{path}: code {code!r} is not registered")
                for severity in sorted(severities):
                    if severity not in SEVERITIES:
                        problems.append(f"{path}: unknown severity {severity!r}")
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
