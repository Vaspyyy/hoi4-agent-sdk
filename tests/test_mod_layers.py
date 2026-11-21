from __future__ import annotations

import ast
import importlib
from pathlib import Path

import hoi4.mod
from hoi4 import Mod

PACKAGE = Path(hoi4.mod.__file__).parent
LAYER_MODULES = sorted(path.stem for path in PACKAGE.glob("_*.py") if path.stem not in {"__init__", "_shared"})


def _class_name(module: str) -> str:
    return "".join(part.capitalize() for part in module.strip("_").split("_")) + "Layer"


def _layer(module: str) -> type:
    return getattr(importlib.import_module(f"hoi4.mod.{module}"), _class_name(module))


def test_every_layer_module_defines_exactly_its_own_layer_class() -> None:
    assert len(LAYER_MODULES) == 30
    for module in LAYER_MODULES:
        tree = ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
        classes = [
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name.endswith("Layer")
        ]
        assert classes == [_class_name(module)]


def test_mod_is_built_from_every_layer_and_nothing_else() -> None:
    layers = {_layer(module) for module in LAYER_MODULES}

    assert set(Mod.__mro__) == {Mod, object, *layers}
    assert _layer("_core").__bases__ == (object,)
    for layer in layers - {_layer("_core")}:
        assert set(layer.__bases__) <= layers
        assert issubclass(layer, _layer("_core"))


def test_a_layer_only_reaches_the_layers_it_names_as_bases() -> None:
    """The class header is the dependency list: unrelated layers stay invisible."""

    events = _layer("_events")
    ideas = _layer("_ideas")
    characters = _layer("_characters")

    assert events.__bases__ == (_layer("_core"),)
    assert not hasattr(events, "create_country")
    assert not hasattr(ideas, "set_state_owner")
    assert not hasattr(characters, "create_country")
    assert hasattr(_layer("_country_authoring"), "create_character")


def _method_owners() -> dict[str, str]:
    owners: dict[str, str] = {}
    for module in LAYER_MODULES:
        for name, member in vars(_layer(module)).items():
            if callable(member) or isinstance(member, (staticmethod, classmethod, property)):
                owners[name] = module
    return owners


def _called_layers() -> dict[str, set[str]]:
    """Which other layer modules each layer module calls into, read from its source."""

    owners = _method_owners()
    calls: dict[str, set[str]] = {}
    for module in LAYER_MODULES:
        tree = ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
        layer_class = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name.endswith("Layer")
        )
        used = {
            owners[node.attr]
            for node in ast.walk(layer_class)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"self", "cls"}
            and node.attr in owners
        }
        calls[module] = used - {module}
    return calls


def test_each_class_header_is_exactly_the_reduced_set_of_layers_it_calls() -> None:
    calls = _called_layers()
    for module in LAYER_MODULES:
        if module != "_core":
            calls[module].add("_core")  # shared state lives there

    def reachable(module: str) -> set[str]:
        seen: set[str] = set()
        stack = list(calls[module])
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(calls[current])
        return seen

    for module in LAYER_MODULES:
        direct = calls[module]
        reduced = {
            dep for dep in direct if not any(dep in reachable(other) for other in direct - {dep})
        }
        bases = {base.__module__.rsplit(".", 1)[1] for base in _layer(module).__bases__ if base is not object}
        assert bases == reduced, f"{module}: header names {sorted(bases)}, code needs {sorted(reduced)}"


def test_the_facade_docstring_lists_what_every_layer_uses() -> None:
    entries = (hoi4.mod.__doc__ or "").split("\n* ``")[1:]
    documented = {
        entry.split("``", 1)[0]: set(" ".join(entry.split()).split(" Uses ", 1)[1].split("``")[1::2])
        for entry in entries
    }

    calls = _called_layers()
    assert set(documented) == set(LAYER_MODULES) - {"_core"}
    for module, listed in documented.items():
        assert listed == (calls[module] - {"_core"} or {"_core"}), module


def test_no_method_is_defined_in_two_layers() -> None:
    seen: dict[str, str] = {}
    for cls in Mod.__mro__[1:-1]:
        for name, member in vars(cls).items():
            if name.startswith("__") and name != "__init__":
                continue
            if not (callable(member) or isinstance(member, (staticmethod, classmethod, property))):
                continue
            assert name not in seen, f"{name} is defined in {seen[name]} and {cls.__name__}"
            seen[name] = cls.__name__
    assert set(vars(Mod)) & set(seen) == {"__init__"}


def test_only_the_core_layer_and_the_facade_define_init() -> None:
    owners = [cls.__name__ for cls in Mod.__mro__[:-1] if "__init__" in vars(cls)]

    assert owners == ["Mod", "CoreLayer"]


def test_mod_module_exports_only_the_supported_names() -> None:
    from hoi4.mod import ExternalModificationError
    from hoi4.mod import Mod as ModFromModule

    assert hoi4.mod.__all__ == ["ExternalModificationError", "Mod"]
    assert ModFromModule is Mod
    assert hoi4.ExternalModificationError is ExternalModificationError
