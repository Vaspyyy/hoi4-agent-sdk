"""The effect, trigger and modifier names a game install knows, and the check that uses them.

Where the names come from:

* the three generated documentation files of the install
  (``documentation/effects_documentation.md``, ``triggers_documentation.md``
  and ``modifiers_documentation.md``): every effect and trigger with the
  scopes it supports, every modifier with its categories;
* the modifier families the documentation writes as templates, such as
  ``production_speed_<Building>_factor``. The documentation lists the
  members it knew when it was generated; the game's own definitions add the
  rest (every building of ``common/buildings``, every ideology of
  ``common/ideologies``, ...), and a mod's definitions add its own;
* ``common/modifier_definitions``: modifiers a game or mod defines itself;
* ``common/scripted_effects`` and ``common/scripted_triggers`` of the game
  and of the mod, which work in any scope;
* ``common/characters``: character ids, which script may use as scopes.

Everything that comes from the install is derived once and cached on disk
(see :func:`load_game_script_vocabulary`). Statements are read with the
scope-tracking walker of :mod:`hoi4.script_scopes`, never with a regular
expression over raw text.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import tempfile
from bisect import bisect_right
import textwrap
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterable, Iterator, Literal, Mapping, Sequence, cast

from .script_scopes import MODIFIER_ARGUMENTS, ScriptStatement, walk_script
from .syntax import Assignment, Block, Tagged, parse
from .textio import read_text
from .types import ValidationError

ScriptTokenKind = Literal["effect", "trigger", "modifier"]

_KINDS: tuple[ScriptTokenKind, ...] = ("effect", "trigger", "modifier")
_DOCUMENTATION_FILES: dict[ScriptTokenKind, str] = {
    "effect": "effects_documentation.md",
    "trigger": "triggers_documentation.md",
    "modifier": "modifiers_documentation.md",
}

# Bump when the derived data or its meaning changes; old cache files are then
# ignored.
_CACHE_SCHEMA = 3
_CACHE_KEEP = 12

# ── modifier families ─────────────────────────────────────────────

#: Where the game defines the members of each placeholder that the modifier
#: documentation uses in its templates. Each entry is a folder (or file) under
#: the content root and how to read it: ``("children", "buildings")`` takes the
#: keys of every ``buildings = { key = { } }`` block, ``("top", None)`` the
#: top-level block keys, ``("values", "technology_categories")`` the bare
#: words of a list, ``("values_of", "slot")`` the values of every
#: ``slot = x`` statement two levels down.
FAMILY_SOURCES: Mapping[str, tuple[tuple[str, str, str | None], ...]] = MappingProxyType(
    {
        "Building": (("common/buildings", "children", "buildings"),),
        "Ideology": (("common/ideologies", "children", "ideologies"),),
        "IdeaGroup": (
            ("common/idea_tags", "children", "idea_categories"),
            ("common/idea_tags", "values_of", "slot"),
            ("common/idea_tags", "values_of", "character_slot"),
        ),
        "IdeaCategory": (("common/idea_tags", "values_of", "type"),),
        "Resource": (("common/resources", "children", "resources"),),
        "Unit": (("common/units", "children", "sub_units"),),
        "Equipment": (("common/units/equipment", "children", "equipments"),),
        "NavalEquipment": (("common/units/equipment", "children", "equipments"),),
        "EquipmentModule": (
            ("common/units/equipment/modules", "children", "equipment_modules"),
        ),
        "Technology": (("common/technology_tags", "values", "technology_categories"),),
        "Trait": (
            ("common/unit_leader", "children", "leader_traits"),
            ("common/country_leader", "children", "leader_traits"),
        ),
        "SpecialProject": (("common/special_projects/projects", "top", None),),
        "Operation": (("common/operations", "top", None),),
        "Doctrine": (
            ("common/doctrines/folders", "top", None),
            ("common/doctrines/grand_doctrines", "top", None),
            ("common/doctrines/subdoctrines", "top", None),
            ("common/doctrines/tracks", "top", None),
        ),
        "CombatTactic": (("common/combat_tactics.txt", "top", None),),
    }
)

#: Families the game generates even when its documentation does not list
#: them (older installs): every ideology has a drift and an acceptance modifier.
BUILTIN_TEMPLATES: tuple[ModifierTemplate, ...] = ()  # filled in below the class

#: Placeholders whose members are triggers of their own: ``infrastructure > 3``
#: (a state's building level), ``fascism > 0.3`` (a party's popularity),
#: ``steel > 10`` (a state's resource).
TRIGGER_FAMILIES = ("Building", "Ideology", "Resource")

_TEMPLATE_HEADING_RE = re.compile(r"</span>\s*(\S+)\s*$")
_PLACEHOLDER_RE = re.compile(r"<([A-Za-z]+)>")
_PLAIN_HEADING_RE = re.compile(r"^[A-Za-z0-9_.:@-]+$")


@dataclass(frozen=True)
class ScriptTokenInfo:
    """One effect, trigger or modifier the configured game (or mod) knows."""

    name: str
    kind: ScriptTokenKind
    supported_scopes: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    usage_count: int = 0
    usage_by_domain: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    #: True for names the documentation does not list by name: scripted
    #: effects and triggers, and modifier family members found in definitions.
    synthesized: bool = False
    supported_targets: tuple[str, ...] = ()
    #: Where the name comes from, such as ``"documentation"``,
    #: ``"production_speed_<Building>_factor from common/buildings"`` or
    #: ``"common/scripted_effects"``.
    source: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "supported_scopes": list(self.supported_scopes),
            "supported_targets": list(self.supported_targets),
            "categories": list(self.categories),
            "usage_count": self.usage_count,
            "usage_by_domain": dict(self.usage_by_domain),
            "synthesized": self.synthesized,
            "source": self.source,
        }


@dataclass(frozen=True)
class ModifierTemplate:
    """A documented modifier family such as ``production_speed_<Building>_factor``."""

    pattern: str
    placeholder: str
    documented_members: tuple[str, ...]
    categories: tuple[str, ...] = ()

    def expand(self, member: str) -> str:
        return self.pattern.replace(f"<{self.placeholder}>", member)


BUILTIN_TEMPLATES = (
    ModifierTemplate("<Ideology>_drift", "Ideology", (), ("country",)),
    ModifierTemplate("<Ideology>_acceptance", "Ideology", (), ("country",)),
)


@dataclass(frozen=True)
class GameScriptVocabulary:
    """Every effect, trigger and modifier name of a game install, with usage counts."""

    hoi4_install: Path
    effects: Mapping[str, ScriptTokenInfo]
    triggers: Mapping[str, ScriptTokenInfo]
    modifiers: Mapping[str, ScriptTokenInfo]
    #: Character ids in lower case, which script may use as scopes
    #: (``GER_erwin_rommel = { }``).
    characters: frozenset[str] = frozenset()
    modifier_templates: tuple[ModifierTemplate, ...] = ()
    #: Members of each template placeholder found in definitions, with the
    #: folder each came from: ``{"Building": {"rail_way": "common/buildings"}}``.
    family_members: Mapping[str, Mapping[str, str]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    _folded: dict[ScriptTokenKind, dict[str, ScriptTokenInfo]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        for kind in _KINDS:
            folded: dict[str, ScriptTokenInfo] = {}
            for name, info in self.tokens(kind).items():
                folded.setdefault(name.lower(), info)
            self._folded[kind] = folded

    def tokens(self, kind: ScriptTokenKind) -> Mapping[str, ScriptTokenInfo]:
        if kind == "effect":
            return self.effects
        if kind == "trigger":
            return self.triggers
        return self.modifiers

    def lookup(self, kind: ScriptTokenKind, name: str) -> ScriptTokenInfo | None:
        """Find a name the way the game does: without regard to case."""

        return self._folded[kind].get(name.lower())

    def folded_names(self, kind: ScriptTokenKind) -> Mapping[str, ScriptTokenInfo]:
        """Every name of a kind in lower case."""

        return self._folded[kind]

    def extended(
        self,
        roots: Iterable[str | Path] = (),
        *,
        scripted_effects: Iterable[str] = (),
        scripted_triggers: Iterable[str] = (),
        ideology_ids: Iterable[str] = (),
        characters: Iterable[str] = (),
    ) -> GameScriptVocabulary:
        """Add what mods define: modifier family members, modifier definitions and more names.

        ``roots`` are mod folders in load order. Their family definitions
        (``common/buildings``, ``common/ideologies``, ...) and
        ``common/modifier_definitions`` are read through the syntax tree. The
        keyword arguments add names the caller already has: scripted effects
        and triggers, ideologies and character ids, including pending edits
        that are not on disk yet.
        """

        extra = _read_definitions([Path(root) for root in roots], usage=False, scripts=False)
        effects = set(scripted_effects)
        triggers = set(scripted_triggers)
        members: dict[str, dict[str, str]] = {
            placeholder: dict(found) for placeholder, found in self.family_members.items()
        }
        for placeholder, found in extra.members.items():
            for member, source in found.items():
                members.setdefault(placeholder, {}).setdefault(member, source)
        for ideology in ideology_ids:
            members.setdefault("Ideology", {}).setdefault(ideology, "common/ideologies")
        new_modifiers = _expand_templates(self.modifier_templates, members)
        for name, source in extra.modifier_definitions.items():
            new_modifiers.setdefault(name, (("country",), source))
        characters = frozenset(name.lower() for name in characters)
        if not (effects or triggers or new_modifiers or characters):
            return self

        def merged(
            kind: ScriptTokenKind,
            additions: Mapping[str, tuple[tuple[str, ...], str]],
        ) -> Mapping[str, ScriptTokenInfo]:
            current = dict(self.tokens(kind))
            folded = self.folded_names(kind)
            for name, (categories, source) in additions.items():
                if name.lower() in folded or name in current:
                    continue
                current[name] = ScriptTokenInfo(
                    name=name,
                    kind=kind,
                    categories=categories,
                    synthesized=True,
                    source=source,
                )
            return MappingProxyType(current)

        return GameScriptVocabulary(
            hoi4_install=self.hoi4_install,
            effects=merged("effect", {name: ((), "mod scripted_effects") for name in effects}),
            triggers=merged(
                "trigger",
                {
                    **{name: ((), "mod scripted_triggers") for name in triggers},
                    **_family_triggers(members),
                },
            ),
            modifiers=merged("modifier", new_modifiers),
            characters=self.characters | characters,
            modifier_templates=self.modifier_templates,
            family_members=MappingProxyType(
                {key: MappingProxyType(value) for key, value in members.items()}
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "hoi4_install": str(self.hoi4_install),
            "effects": {key: value.to_dict() for key, value in self.effects.items()},
            "triggers": {key: value.to_dict() for key, value in self.triggers.items()},
            "modifiers": {key: value.to_dict() for key, value in self.modifiers.items()},
        }


@dataclass(frozen=True)
class ScriptSource:
    """One block of script to check: its text, what kind of block it is and where it lives.

    ``scope`` is the scope the block runs in when that is certain (``"COUNTRY"``
    for a focus reward), else ``None``. ``from_scope`` is what ``FROM`` points
    to, when known. ``line`` is the line of the file on which the text's first
    line stands, when known; findings then carry file lines. Without a line
    and a file, findings carry lines of the text itself.
    """

    text: str
    kind: ScriptTokenKind | Literal["weight"]
    owner_kind: str = ""
    owner_id: str = ""
    file_path: str | None = None
    scope: str | None = None
    from_scope: str | None = None
    line: int | None = None
    #: The file line of every line of ``text``, when known (see
    #: :class:`SourceLocator`); it wins over ``line``.
    line_map: tuple[int, ...] = ()


# ── loading ───────────────────────────────────────────────────────


def load_game_script_vocabulary(
    hoi4_install: str | Path,
    *,
    ideology_ids: Iterable[str] = (),
    cache: bool = True,
) -> GameScriptVocabulary:
    """Read the vocabulary of a game install.

    What is derived from the install is cached under the user cache folder
    (``~/.cache/hoi4-sdk/script-vocabulary``, or ``$HOI4_SDK_CACHE_DIR``). The
    cache key covers the SDK version, this module's source and the size and
    modification time of every file the vocabulary is read from, so a game
    update or a new SDK reads the install again. Pass ``cache=False`` to
    neither read nor write the cache.
    """

    root = Path(hoi4_install).resolve()
    data = _cached_install_data(root) if cache else None
    if data is None:
        data = _install_data(root)
        if cache:
            _store_install_data(root, data)
    vocabulary = _vocabulary_from_data(root, data)
    ideologies = tuple(ideology_ids)
    if ideologies:
        vocabulary = vocabulary.extended(ideology_ids=ideologies)
    return vocabulary


def _cache_dir() -> Path:
    configured = os.environ.get("HOI4_SDK_CACHE_DIR")
    base = Path(configured) if configured else Path.home() / ".cache" / "hoi4-sdk"
    return base / "script-vocabulary"


def _sdk_version() -> str:
    try:
        from importlib.metadata import version

        return version("hoi4-agent-sdk")
    except Exception:  # pragma: no cover - not installed as a package
        return "unknown"


def _install_files(root: Path) -> Iterator[Path]:
    documentation = root / "documentation"
    for name in _DOCUMENTATION_FILES.values():
        path = documentation / name
        if path.is_file():
            yield path
    for top in ("common", "events", "history"):
        directory = root / top
        if directory.is_dir():
            yield from directory.rglob("*.txt")


def _cache_key(root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(f"{_CACHE_SCHEMA}\0{_sdk_version()}\0{root}\0".encode())
    entries = []
    for path in _install_files(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append(f"{path.relative_to(root).as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}")
    for entry in sorted(entries):
        digest.update(entry.encode("utf-8", "surrogateescape"))
        digest.update(b"\n")
    return digest.hexdigest()[:32]


def _cached_install_data(root: Path) -> dict | None:
    try:
        path = _cache_dir() / f"{_cache_key(root)}.json"
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        os.utime(path)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != _CACHE_SCHEMA:
        return None
    return data


def _store_install_data(root: Path, data: dict) -> None:
    directory = _cache_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{_cache_key(root)}.json"
        handle, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, separators=(",", ":"))
        os.replace(temporary, target)
        cached = sorted(
            directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True
        )
        for old in cached[_CACHE_KEEP:]:
            old.unlink(missing_ok=True)
    except OSError:
        return


def _install_data(root: Path) -> dict:
    """Everything the vocabulary needs from an install, as plain JSON data."""

    documentation = root / "documentation"
    docs: dict[str, dict[str, list[list[str]]]] = {}
    templates: list[list[object]] = []
    for kind in _KINDS:
        path = documentation / _DOCUMENTATION_FILES[kind]
        entries, kind_templates = _documentation_entries(path)
        docs[kind] = {
            name: [list(scopes), list(targets), list(categories)]
            for name, (scopes, targets, categories) in entries.items()
        }
        if kind == "modifier":
            templates = [
                [item.pattern, item.placeholder, list(item.documented_members), list(item.categories)]
                for item in kind_templates
            ]
    definitions = _read_definitions([root], usage=True)
    return {
        "schema": _CACHE_SCHEMA,
        "docs": docs,
        "templates": templates,
        "members": {key: dict(value) for key, value in definitions.members.items()},
        "modifier_definitions": dict(definitions.modifier_definitions),
        "scripted": {key: sorted(value) for key, value in definitions.scripted.items()},
        "characters": sorted(definitions.characters),
        "usage": {
            name: [count, dict(definitions.domains[name])]
            for name, count in definitions.usage.items()
        },
    }


def _vocabulary_from_data(root: Path, data: dict) -> GameScriptVocabulary:
    usage: dict[str, list] = data.get("usage", {})
    templates = tuple(
        ModifierTemplate(
            pattern=str(pattern),
            placeholder=str(placeholder),
            documented_members=tuple(cast(list, members)),
            categories=tuple(cast(list, categories)),
        )
        for pattern, placeholder, members, categories in data.get("templates", [])
    )
    documented_patterns = {template.pattern for template in templates}
    templates += tuple(
        template for template in BUILTIN_TEMPLATES if template.pattern not in documented_patterns
    )
    members: dict[str, dict[str, str]] = {
        str(key): dict(value) for key, value in data.get("members", {}).items()
    }

    def info(
        name: str,
        kind: ScriptTokenKind,
        *,
        scopes: Sequence[str] = (),
        targets: Sequence[str] = (),
        categories: Sequence[str] = (),
        synthesized: bool = False,
        source: str = "documentation",
    ) -> ScriptTokenInfo:
        count, domains = usage.get(name.lower(), (0, {}))
        return ScriptTokenInfo(
            name=name,
            kind=kind,
            supported_scopes=tuple(scopes),
            supported_targets=tuple(targets),
            categories=tuple(categories),
            usage_count=count,
            usage_by_domain=MappingProxyType(cast(dict[str, int], domains)),
            synthesized=synthesized,
            source=source,
        )

    tables: dict[ScriptTokenKind, dict[str, ScriptTokenInfo]] = {kind: {} for kind in _KINDS}
    for kind in _KINDS:
        for name, (scopes, targets, categories) in data["docs"].get(kind, {}).items():
            tables[kind][name] = info(
                name, kind, scopes=scopes, targets=targets, categories=categories
            )
    for kind_name, names in data.get("scripted", {}).items():
        kind = cast(ScriptTokenKind, kind_name)
        for name in names:
            tables[kind].setdefault(
                name, info(name, kind, synthesized=True, source=f"common/scripted_{kind}s")
            )
    for name, (_, source) in _family_triggers(members).items():
        tables["trigger"].setdefault(
            name, info(name, "trigger", synthesized=True, source=source)
        )
    modifiers = tables["modifier"]
    folded_modifiers = {name.lower() for name in modifiers}
    documented_family: dict[str, tuple[tuple[str, ...], str]] = {}
    for template in templates:
        for member in template.documented_members:
            documented_family.setdefault(
                template.expand(member), (template.categories, f"{template.pattern} (documented)")
            )
    for name, (categories, source) in documented_family.items():
        if name.lower() not in folded_modifiers:
            modifiers[name] = info(name, "modifier", categories=categories, source=source)
            folded_modifiers.add(name.lower())
    extra = _expand_templates(templates, members)
    for name, source in data.get("modifier_definitions", {}).items():
        extra.setdefault(name, (("country",), source))
    for name, (categories, source) in extra.items():
        if name.lower() not in folded_modifiers:
            modifiers[name] = info(
                name, "modifier", categories=categories, synthesized=True, source=source
            )
            folded_modifiers.add(name.lower())
    return GameScriptVocabulary(
        hoi4_install=root,
        effects=MappingProxyType(dict(sorted(tables["effect"].items()))),
        triggers=MappingProxyType(dict(sorted(tables["trigger"].items()))),
        modifiers=MappingProxyType(dict(sorted(modifiers.items()))),
        characters=frozenset(data.get("characters", ())),
        modifier_templates=templates,
        family_members=MappingProxyType(
            {key: MappingProxyType(value) for key, value in members.items()}
        ),
    )


def _family_triggers(
    members: Mapping[str, Mapping[str, str]],
) -> dict[str, tuple[tuple[str, ...], str]]:
    result: dict[str, tuple[tuple[str, ...], str]] = {}
    for placeholder in TRIGGER_FAMILIES:
        for member, source in members.get(placeholder, {}).items():
            result.setdefault(member, ((), f"<{placeholder}> trigger from {source}"))
    return result


def _expand_templates(
    templates: Iterable[ModifierTemplate],
    members: Mapping[str, Mapping[str, str]],
) -> dict[str, tuple[tuple[str, ...], str]]:
    """Names of every template's family members found in definitions.

    A documented template only learns members from the folders its documented
    members come from: ``<Doctrine>_track_mastery_gain_factor`` lists doctrine
    tracks, so it takes new members from ``common/doctrines/tracks`` and not
    from the grand doctrines another ``<Doctrine>`` template lists. A template
    none of whose documented members is defined anywhere learns nothing.
    """

    result: dict[str, tuple[tuple[str, ...], str]] = {}
    for template in templates:
        found = members.get(template.placeholder, {})
        allowed: set[str] | None = None
        if template.documented_members:
            allowed = {found[name] for name in template.documented_members if name in found}
            if not allowed:
                continue
        for member, source in found.items():
            if allowed is None or source in allowed:
                result.setdefault(
                    template.expand(member),
                    (template.categories, f"{template.pattern} from {source}"),
                )
    return result


# ── documentation ─────────────────────────────────────────────────


def _documentation_entries(
    path: Path,
) -> tuple[
    dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]],
    list[ModifierTemplate],
]:
    """Read one generated documentation file.

    Returns ``{name: (scopes, targets, categories)}`` for every plain entry and
    the templated modifier families such as ``production_speed_<Building>_factor``.
    """

    if not path.is_file():
        return {}, []
    entries: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {}
    templates: list[ModifierTemplate] = []
    heading: str | None = None
    lines: list[str] = []

    def finish() -> None:
        if heading is None:
            return
        scopes = _metadata_values(lines, "Supported Scopes")
        targets = _metadata_values(lines, "Supported Targets")
        categories = _metadata_values(lines, "Categories")
        template = _TEMPLATE_HEADING_RE.search(heading)
        if template is not None:
            pattern = template.group(1)
            placeholder = _PLACEHOLDER_RE.search(pattern)
            if placeholder is not None:
                templates.append(
                    ModifierTemplate(
                        pattern=pattern,
                        placeholder=placeholder.group(1),
                        documented_members=_metadata_values(lines, "Modified types"),
                        categories=categories,
                    )
                )
            return
        name = heading.strip()
        if _PLAIN_HEADING_RE.match(name) and name != "Table":
            entries.setdefault(name, (scopes, targets, categories))

    for line in read_text(path).split("\n"):
        if line.startswith("## "):
            finish()
            heading = line[3:]
            lines = []
        elif line.startswith("# "):
            finish()
            heading = None
        elif heading is not None:
            lines.append(line)
    finish()
    entries.pop("Table", None)
    return entries, templates


def _metadata_values(lines: Sequence[str], label: str) -> tuple[str, ...]:
    """The comma-separated values of a ``* Label: a, b`` line of a documentation entry."""

    wanted = label.lower()
    for line in lines:
        text = line.strip()
        if not text.startswith("*"):
            continue
        text = text.lstrip("*").strip().replace("**", "")
        name, colon, rest = text.partition(":")
        if not colon or name.strip().lower() != wanted:
            continue
        values = []
        for value in re.split(r"[,|]", rest):
            value = value.strip().strip("`*").strip()
            if value and value.lower() != "none":
                values.append(value)
        return tuple(values)
    return ()


# ── definitions read through the tree ─────────────────────────────


@dataclass
class _Definitions:
    members: dict[str, dict[str, str]] = field(default_factory=dict)
    modifier_definitions: dict[str, str] = field(default_factory=dict)
    scripted: dict[str, set[str]] = field(
        default_factory=lambda: {"effect": set(), "trigger": set()}
    )
    characters: frozenset[str] = frozenset()
    usage: Counter[str] = field(default_factory=Counter)
    domains: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))


def _script_files(root: Path, relative: str) -> list[Path]:
    path = root / relative
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(item for item in path.rglob("*.txt") if item.is_file())
    return []


def _root_block(path: Path) -> Block | None:
    try:
        return parse(read_text(path)).root
    except (OSError, ValueError):
        return None


def _read_definitions(
    roots: Sequence[Path], *, usage: bool, scripts: bool = True
) -> _Definitions:
    result = _Definitions()
    documents: dict[Path, Block | None] = {}

    def block_of(path: Path) -> Block | None:
        if path not in documents:
            documents[path] = _root_block(path)
        return documents[path]

    for root in roots:
        for placeholder, sources in FAMILY_SOURCES.items():
            found = result.members.setdefault(placeholder, {})
            for relative, how, key in sources:
                for path in _script_files(root, relative):
                    block = block_of(path)
                    if block is None:
                        continue
                    for member in _family_members(block, how, key):
                        found.setdefault(member, relative)
        for path in _script_files(root, "common/modifier_definitions"):
            block = block_of(path)
            if block is not None:
                for item in block.assignments():
                    if item.block is not None:
                        result.modifier_definitions.setdefault(
                            item.name, "common/modifier_definitions"
                        )
        for kind in ("effect", "trigger") if scripts else ():
            for path in _script_files(root, f"common/scripted_{kind}s"):
                block = block_of(path)
                if block is not None:
                    result.scripted[kind].update(
                        item.name for item in block.assignments() if item.block is not None
                    )
    characters: set[str] = set()  # folded to lower case
    for root in roots if scripts else ():
        for path in _script_files(root, "common/characters"):
            block = block_of(path)
            if block is None:
                continue
            for container in block.assignments():
                if container.block is not None:
                    characters.update(
                        item.name.lower() for item in container.block.assignments()
                        if item.block is not None
                    )
    result.characters = frozenset(characters)
    result.members = {key: value for key, value in result.members.items() if value}
    if usage:
        for root in roots:
            _count_usage(root, result)
    return result


def _family_members(block: Block, how: str, key: str | None) -> Iterator[str]:
    if how == "top":
        for item in block.assignments():
            if item.block is not None and not item.name.startswith("@"):
                if item.name != "phases":
                    yield item.name
        return
    if how == "children":
        for container in block.assignments(key):
            inner = container.block
            if inner is None:
                continue
            for item in inner.assignments():
                if item.block is not None:
                    yield item.name
        return
    if how == "values":
        for container in block.assignments(key):
            inner = container.block
            if inner is not None:
                for scalar in inner.values():
                    yield scalar.value
        return
    if how == "values_of":
        for container in block.assignments():
            outer = container.block
            if outer is None:
                continue
            for category in outer.assignments():
                inner = category.block
                if inner is None:
                    continue
                for item in inner.assignments(key):
                    value = item.scalar
                    if value:
                        yield value


def _count_usage(root: Path, result: _Definitions) -> None:
    """Count every statement key of the install's script, folded to lower case.

    This reads the whole install through the tree (about 80 MB of script), so
    it only runs when the on-disk cache is missing or stale. It stays in this
    process on purpose: worker processes would re-import a caller's script
    that has no ``if __name__ == "__main__"`` guard.
    """

    files: list[str] = []
    for top in ("common", "events", "history"):
        directory = root / top
        if directory.is_dir():
            files.extend(str(path) for path in sorted(directory.rglob("*.txt")))
    counts, domains = _count_files(str(root), files)
    result.usage.update(counts)
    for name, per_domain in domains.items():
        result.domains[name].update(per_domain)


def _count_files(root_text: str, paths: list[str]) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Statement keys of some files, in total and per folder."""

    root = Path(root_text)
    counts: Counter[str] = Counter()
    domains: dict[str, Counter[str]] = defaultdict(Counter)
    for text_path in paths:
        path = Path(text_path)
        block = _root_block(path)
        if block is None:
            continue
        domain = "/".join(path.relative_to(root).parts[:2])
        local: Counter[str] = Counter()
        stack = [block]
        while stack:
            current = stack.pop()
            for item in current.items:
                kind = type(item)
                if kind is Assignment:
                    assert isinstance(item, Assignment)
                    local[item.key.value.lower()] += 1
                    value = item.value
                    if type(value) is Block:
                        stack.append(value)
                    elif type(value) is Tagged:
                        stack.append(value.block)
                elif kind is Block:
                    assert isinstance(item, Block)
                    stack.append(item)
                elif kind is Tagged:
                    assert isinstance(item, Tagged)
                    stack.append(item.block)
        counts.update(local)
        for name, count in local.items():
            domains[name][domain] += count
    return dict(counts), {name: dict(value) for name, value in domains.items()}


# ── validation ────────────────────────────────────────────────────


def validate_script_sources(
    sources: Iterable[ScriptSource],
    vocabulary: GameScriptVocabulary,
    *,
    mod_root: str | Path | None = None,
    custom_tokens: Mapping[ScriptTokenKind, set[str]] | None = None,
    allowlist: Iterable[str] = (),
) -> list[ValidationError]:
    """Report names the game does not know and names used in a scope they do not support.

    Codes (all with a file and a line when the source has them):

    * ``unknown_effect_token``, ``unknown_trigger_token``,
      ``unknown_modifier_token`` (warning): a name neither the game, its
      definitions nor the mod knows.
    * ``unsupported_effect_scope``, ``unsupported_trigger_scope`` (warning):
      a documented name used in a scope its documentation does not list. Only
      reported when the scope is certain.
    * ``unseen_effect_token``, ``unseen_trigger_token``,
      ``unseen_modifier_token`` (info): a documented name the game's own
      script never uses. It is legal, but nobody has tried it.
    """

    allowed = tuple(dict.fromkeys(allowlist))
    custom = (
        custom_tokens
        if custom_tokens is not None
        else _custom_script_tokens(Path(mod_root))
        if mod_root is not None
        else {}
    )
    custom_folded = {
        kind: {name.lower() for name in names} for kind, names in custom.items()
    }
    characters = vocabulary.characters
    known_scripted = custom_folded.get("effect", set()) | custom_folded.get("trigger", set())
    findings: list[ValidationError] = []
    seen: set[tuple[str, str, str, int | None]] = set()
    for source in sources:
        if not source.text.strip():
            continue
        statements = walk_script(
            source.text,
            source.kind,
            scope=source.scope.upper() if source.scope else None,
            from_scope=source.from_scope.upper() if source.from_scope else None,
            is_command=lambda kind, folded: (
                folded in vocabulary.folded_names(kind) or folded in custom_folded.get(kind, ())
                or (kind != "modifier" and folded in known_scripted)
            ),
            characters=characters,
        )
        for statement in statements:
            if 0 < statement.line <= len(source.line_map):
                line = source.line_map[statement.line - 1]
            else:
                line = statement.line + (source.line - 1 if source.line is not None else 0)
            finding = check_statement(
                statement, source, line, vocabulary, custom_folded, allowed
            )
            if finding is None:
                continue
            # The same text can be checked twice (an on-action's event list).
            identity = (finding.code, finding.message, finding.file_path or source.owner_id, line)
            if identity not in seen:
                seen.add(identity)
                findings.append(finding)
    return findings


def check_statement(
    statement: ScriptStatement,
    source: ScriptSource,
    line: int,
    vocabulary: GameScriptVocabulary,
    custom: Mapping[ScriptTokenKind, set[str]],
    allowed: tuple[str, ...],
) -> ValidationError | None:
    """Check one statement found by the walker; ``None`` when it is fine.

    ``custom`` holds extra names per kind in lower case, ``allowed`` the
    allowlist patterns (``effect:my_effect``).
    """

    kind = cast(ScriptTokenKind, statement.kind)
    name = statement.name
    folded = name.lower()
    if not vocabulary.tokens(kind):
        return None
    if _is_allowed(name, kind, allowed):
        return None
    info = vocabulary.lookup(kind, name)
    owner = (
        f"{source.owner_kind} '{source.owner_id}' "
        if source.owner_kind and source.owner_id
        else ""
    )
    if info is None:
        if folded in custom.get(kind, ()) or (
            kind != "modifier"
            and folded in (custom.get("effect", set()) | custom.get("trigger", set()))
        ):
            return None
        suggestion = _nearest(name, vocabulary.tokens(kind))
        hint = ""
        if suggestion is not None:
            hint = (
                f" Did you mean '{suggestion}' "
                f"({vocabulary.tokens(kind)[suggestion].usage_count} installed-game uses)?"
            )
        return _finding(
            source,
            line,
            code=f"unknown_{kind}_token",
            severity="warning",
            message=(
                f"{owner}uses undocumented {kind} token '{name}' (0 installed-game uses)."
                f"{hint} Allow an intentional extension with '{kind}:{name}'."
            ),
        )
    if statement.scope is not None and _unsupported_scope(statement.scope, info.supported_scopes):
        return _finding(
            source,
            line,
            code=f"unsupported_{kind}_scope",
            severity="warning",
            message=(
                f"{owner}uses {kind} token '{name}' in {statement.scope} scope, but the "
                f"installed game documents supported scopes: "
                f"{', '.join(info.supported_scopes)}."
            ),
        )
    if info.usage_count or info.synthesized:
        return None
    return _finding(
        source,
        line,
        code=f"unseen_{kind}_token",
        severity="info",
        message=(
            f"{owner}uses documented {kind} token '{name}', but it has 0 "
            "installed-game uses. This may be a legal unexercised token; "
            f"verify it or allow '{kind}:{name}'."
        ),
    )


def _finding(
    source: ScriptSource, line: int, *, code: str, severity: str, message: str
) -> ValidationError:
    owner_kind = source.owner_kind
    owner_id = source.owner_id or None
    return ValidationError(
        message=message,
        severity=severity,
        code=code,
        file_path=source.file_path,
        line=line,
        focus_id=owner_id if owner_kind == "focus" else None,
        event_id=owner_id if owner_kind == "event" else None,
        idea_id=owner_id if owner_kind == "idea" else None,
        decision_id=owner_id if owner_kind == "decision" else None,
        dynamic_modifier_id=owner_id if owner_kind == "dynamic_modifier" else None,
        bookmark_name=owner_id if owner_kind == "bookmark" else None,
    )


def _unsupported_scope(actual: str, supported: tuple[str, ...]) -> bool:
    if not supported:
        return False
    normalized = {value.upper() for value in supported}
    return "ANY" not in normalized and actual.upper() not in normalized


def _custom_script_tokens(mod_root: Path) -> dict[ScriptTokenKind, set[str]]:
    definitions = _read_definitions([mod_root], usage=False)
    return {
        "effect": set(definitions.scripted["effect"]),
        "trigger": set(definitions.scripted["trigger"]),
        "modifier": set(definitions.modifier_definitions),
    }


def _is_allowed(name: str, kind: ScriptTokenKind, patterns: tuple[str, ...]) -> bool:
    return any(
        fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(f"{kind}:{name}", pattern)
        for pattern in patterns
    )


def _nearest(name: str, known: Mapping[str, ScriptTokenInfo]) -> str | None:
    from difflib import SequenceMatcher, get_close_matches

    candidates = get_close_matches(name, known.keys(), n=3, cutoff=0.55)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda candidate: (
            SequenceMatcher(None, name, candidate).ratio(),
            known[candidate].usage_count,
        ),
    )


def modifier_block_text(
    raw_block: str,
    key: str | None,
    modifiers: Mapping[str, object],
) -> str:
    """The text of a definition's modifier block, as it stands in its file when unchanged.

    ``raw_block`` is the definition's own body as read from its file and
    ``key`` the key of its modifier block (``None`` when the definition's
    body is the modifier block, as for dynamic modifiers). ``modifiers`` is
    the model's view. When the file's block has the same modifier names, its
    text is used (so findings get file lines and nested ``hidden_modifier``
    blocks are checked); otherwise the text is made from the model.
    """

    if raw_block:
        root = parse(raw_block).root
        block = root.block(key) if key is not None else root
        if block is not None:
            names = {
                item.name
                for item in block.assignments()
                if item.block is None and (key is not None or item.name not in MODIFIER_ARGUMENTS)
            }
            if names == set(modifiers):
                return textwrap.dedent(block.body).strip()
    return "\n".join(f"{name} = {value}" for name, value in modifiers.items())


class SourceLocator:
    """Find where copied block bodies stand in their files.

    Model fields hold block bodies with their indentation removed, and an
    event option's effect leaves out its ``name``, ``trigger`` and
    ``ai_chance``. :meth:`line_map` finds such a text inside the text of its
    owning definition (a model's ``raw_block``) line by line, ignoring leading
    and trailing spaces and allowing lines of the file to be skipped, and
    returns the file line of every line of the text.
    """

    def __init__(self, read: Callable[[Path], str]) -> None:
        self._read = read
        self._texts: dict[Path, str] = {}
        self._line_starts: dict[Path, list[int]] = {}
        self._last_anchor: dict[Path, int] = {}

    def text(self, path: Path) -> str:
        if path not in self._texts:
            try:
                self._texts[path] = self._read(path)
            except (OSError, ValueError):
                self._texts[path] = ""
        return self._texts[path]

    def line_of(self, path: Path, offset: int) -> int:
        starts = self._line_starts.get(path)
        if starts is None:
            content = self.text(path)
            starts = [0]
            position = content.find("\n")
            while position >= 0:
                starts.append(position + 1)
                position = content.find("\n", position + 1)
            self._line_starts[path] = starts
        return bisect_right(starts, offset)

    def line_map(self, path: Path, text: str, anchor: str) -> tuple[int, ...] | None:
        """File lines of every line of ``text``, or ``None`` when it cannot be found.

        When only ``anchor`` is found (the text was edited after loading), every
        line maps to the anchor's first line.
        """

        content = self.text(path)
        wanted = [line.strip() for line in text.strip().split("\n")]
        if not content or not anchor or not wanted[0]:
            return None
        position = content.find(anchor, self._last_anchor.get(path, 0))
        if position < 0:
            position = content.find(anchor)
        if position < 0:
            return None
        self._last_anchor[path] = position
        high = position + len(anchor)
        # First where the text's first line starts a line of the file; then
        # where it ends one: ``name = x #note`` leaves ``#note`` as the first
        # line of an option's effect, and ``ai_will_do = { factor = 1 }`` puts
        # the whole body on its key's line.
        # A copy that runs line for line beats one that needs skipped lines:
        # two fields of one decision may start with the same line.
        for skip, inline in ((False, False), (False, True), (True, False), (True, True)):
            first = content.find(wanted[0], position, high)
            while first >= 0:
                line_start = content.rfind("\n", 0, first) + 1
                line_end = content.find("\n", first)
                if line_end < 0:
                    line_end = len(content)
                before = content[line_start:first].strip()
                after = content[first + len(wanted[0]) : line_end]
                if (bool(before) == inline) and _only_closing(after):
                    offsets = self._follow(content, line_end + 1, high, wanted[1:], skip)
                    if offsets is not None:
                        return tuple(
                            self.line_of(path, offset) for offset in (line_start, *offsets)
                        )
                first = content.find(wanted[0], first + 1, high)
        return (self.line_of(path, position),) * len(wanted)

    @staticmethod
    def _follow(
        content: str, offset: int, high: int, wanted: list[str], skip: bool
    ) -> list[int] | None:
        """Match ``wanted`` line by line from ``offset``; ``skip`` allows lines it does not hold.

        Blank lines and comment-only lines of the file are always skipped,
        since a field's text may have lost them.
        """

        found: list[int] = []
        for expected in wanted:
            while offset < high:
                end = content.find("\n", offset)
                if end < 0:
                    end = len(content)
                line = content[offset:end].strip()
                start, offset = offset, end + 1
                if line == expected or (
                    expected and line.startswith(expected) and _only_closing(line[len(expected):])
                ):
                    found.append(start)
                    break
                if not skip and line and not line.startswith("#"):
                    return None
            else:
                return None
        return found


def _only_closing(text: str) -> bool:
    """Whether ``text`` holds nothing but spaces and closing braces."""

    return not text.replace("}", "").strip()


__all__ = [
    "FAMILY_SOURCES",
    "GameScriptVocabulary",
    "ModifierTemplate",
    "ScriptSource",
    "ScriptTokenInfo",
    "ScriptTokenKind",
    "load_game_script_vocabulary",
    "SourceLocator",
    "check_statement",
    "modifier_block_text",
    "validate_script_sources",
]
