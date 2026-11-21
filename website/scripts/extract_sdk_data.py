#!/usr/bin/env python3
"""Extract live metadata, API surface, architecture, docs, and releases from hoi4-agent-sdk."""

from __future__ import annotations

import inspect
import json
import pathlib
import re
import sys
import tomllib
from typing import Any

WEBSITE_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = WEBSITE_DIR.parent
OUT_DIR = WEBSITE_DIR / "src" / "generated"

sys.path.insert(0, str(REPO_ROOT / "src"))

import hoi4  # noqa: E402
from hoi4.definitions import DEFINITION_KINDS  # noqa: E402
from hoi4.effects_catalog import EFFECT_CATEGORIES, TECHNOLOGY_CATEGORIES  # noqa: E402
from hoi4.modifiers_catalog import MODIFIER_CATEGORIES  # noqa: E402
from hoi4.validation import VALIDATION_CODES, VALIDATION_WARNING_CODES  # noqa: E402


def slugify(text: str) -> str:
    clean = re.sub(r"`([^`]*)`", r"\1", text)
    clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)
    clean = re.sub(r"[^a-zA-Z0-9\s-]", "", clean).strip().lower()
    return re.sub(r"[\s_-]+", "-", clean) or "section"


def classify_validation_code(code: str, desc: str) -> str:
    c = code.lower()
    d = desc.lower()
    if c.startswith("duplicate_"):
        return "Duplicates"
    if "loc" in c or "translation" in c or "localization" in d:
        return "Localisation"
    if any(k in c for k in ("oob", "division", "ship", "fleet", "air_wing", "hull", "chassis", "equipment", "variant", "battalion", "template")):
        return "OOB & Equipment"
    if any(k in c for k in ("character", "leader", "advisor", "portrait", "country", "tag", "package", "name_pool", "flag", "ideology", "recruit")):
        return "Countries & Characters"
    if any(k in c for k in ("state", "province", "strategic_region", "railway", "supply", "map", "territory", "enclosed", "disconnected")):
        return "States & Map"
    if any(k in c for k in ("focus", "event", "on_action", "civil_war", "revolt", "faction", "resistance")):
        return "Focuses & Events"
    if any(k in c for k in ("decision", "mission", "idea", "dynamic_modifier", "bookmark")):
        return "Decisions & Ideas"
    if any(k in c for k in ("effect", "trigger", "modifier", "scope", "syntax", "tech", "wargoal", "token", "script")):
        return "Script & Vocabulary"
    if any(k in c for k in ("liveness", "unused", "unreachable", "unfired", "ungranted", "asymmetric")):
        return "Content Liveness"
    if any(k in c for k in ("log", "load", "icon", "sprite")):
        return "Engine Log & Assets"
    return "General"


LAYER_TIERS: dict[str, str] = {
    "_core": "foundation",
    "_sources": "foundation",
    "_catalogs": "foundation",
    "_loading": "foundation",
    "_localisation": "foundation",
    "_effects": "foundation",
    "_countries": "domains",
    "_characters": "domains",
    "_country_authoring": "domains",
    "_oobs": "domains",
    "_equipment": "domains",
    "_states": "domains",
    "_map": "domains",
    "_focus": "domains",
    "_events": "domains",
    "_on_actions": "domains",
    "_decisions": "domains",
    "_ideas": "domains",
    "_ideologies": "domains",
    "_dynamic_modifiers": "domains",
    "_bookmarks": "domains",
    "_definitions": "domains",
    "_assets": "domains",
    "_context": "domains",
    "_script_validation": "validation",
    "_oob_validation": "validation",
    "_country_package": "validation",
    "_validation": "validation",
    "_preview": "persistence",
    "_persistence": "persistence",
}


def parse_layer_descriptions() -> dict[str, str]:
    init_text = (REPO_ROOT / "src" / "hoi4" / "mod" / "__init__.py").read_text(encoding="utf-8")
    descriptions: dict[str, str] = {}
    pattern = re.compile(r"^\*\s+``(_[a-z_]+)``:\s*(.+?)(?=\n\*\s+``|\n\"\"\")", re.MULTILINE | re.DOTALL)
    for match in pattern.finditer(init_text):
        mod_name = match.group(1)
        raw = " ".join(line.strip() for line in match.group(2).splitlines())
        raw = re.sub(r"\s*Uses\s+(?:only\s+)?``.+?``\.\s*$", "", raw)
        descriptions[mod_name] = raw.strip()
    return descriptions


def extract_layers() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    descriptions = parse_layer_descriptions()
    mod_dir = REPO_ROOT / "src" / "hoi4" / "mod"
    layer_classes: dict[str, type] = {}

    for cls in hoi4.Mod.__mro__:
        if cls is hoi4.Mod or cls is object:
            continue
        mod_short = cls.__module__.split(".")[-1]
        if mod_short.startswith("_"):
            layer_classes[mod_short] = cls

    # Map class -> module short name
    cls_to_mod = {cls: name for name, cls in layer_classes.items()}

    layers_out: list[dict[str, Any]] = []
    all_methods_out: list[dict[str, Any]] = []

    for mod_short, cls in sorted(
        layer_classes.items(),
        key=lambda item: (
            ["foundation", "domains", "validation", "persistence"].index(
                LAYER_TIERS.get(item[0], "domains")
            ),
            item[0],
        ),
    ):
        file_path = mod_dir / f"{mod_short}.py"
        line_count = len(file_path.read_text(encoding="utf-8").splitlines()) if file_path.exists() else 0

        direct_bases = [cls_to_mod[b] for b in cls.__bases__ if b in cls_to_mod]
        transitive_bases = [
            cls_to_mod[b]
            for b in cls.__mro__[1:]
            if b in cls_to_mod
        ]

        methods: list[dict[str, Any]] = []
        for attr_name, raw_attr in cls.__dict__.items():
            if attr_name.startswith("_"):
                continue
            kind = "method"
            fn = raw_attr
            if isinstance(raw_attr, staticmethod):
                kind = "staticmethod"
                fn = raw_attr.__func__
            elif isinstance(raw_attr, classmethod):
                kind = "classmethod"
                fn = raw_attr.__func__
            elif isinstance(raw_attr, property):
                kind = "property"
                fn = raw_attr.fget
            if not callable(fn):
                continue

            try:
                sig = str(inspect.signature(fn))
            except (TypeError, ValueError):
                sig = "()"
            sig = re.sub(r"^\(self,?\s*", "(", sig)
            sig = re.sub(r"^\(cls,?\s*", "(", sig)

            doc = inspect.getdoc(fn) or ""
            summary = doc.splitlines()[0].strip() if doc else ""

            method_info = {
                "name": attr_name,
                "layer": mod_short,
                "layerClass": cls.__name__,
                "tier": LAYER_TIERS.get(mod_short, "domains"),
                "kind": kind,
                "signature": f"{attr_name}{sig}",
                "summary": summary,
                "doc": doc,
            }
            methods.append(method_info)
            all_methods_out.append(method_info)

        methods.sort(key=lambda m: m["name"])
        layers_out.append(
            {
                "id": mod_short,
                "className": cls.__name__,
                "filePath": f"src/hoi4/mod/{mod_short}.py",
                "tier": LAYER_TIERS.get(mod_short, "domains"),
                "lines": line_count,
                "description": descriptions.get(mod_short, inspect.getdoc(cls) or ""),
                "directBases": direct_bases,
                "transitiveBases": transitive_bases,
                "dependents": [],
                "methodCount": len(methods),
                "methods": methods,
            }
        )

    # Also include methods defined directly on Mod itself (e.g., from_config)
    mod_direct_methods: list[dict[str, Any]] = []
    for attr_name, raw_attr in hoi4.Mod.__dict__.items():
        if attr_name.startswith("_"):
            continue
        kind = "classmethod" if isinstance(raw_attr, classmethod) else "method"
        fn = raw_attr.__func__ if isinstance(raw_attr, (classmethod, staticmethod)) else raw_attr
        if not callable(fn):
            continue
        try:
            sig = str(inspect.signature(fn))
        except (TypeError, ValueError):
            sig = "()"
        sig = re.sub(r"^\((?:self|cls),?\s*", "(", sig)
        doc = inspect.getdoc(fn) or ""
        info = {
            "name": attr_name,
            "layer": "Mod",
            "layerClass": "Mod",
            "tier": "foundation",
            "kind": kind,
            "signature": f"{attr_name}{sig}",
            "summary": doc.splitlines()[0].strip() if doc else "",
            "doc": doc,
        }
        mod_direct_methods.append(info)
        all_methods_out.append(info)

    # Compute direct dependents
    layer_by_id = {layer["id"]: layer for layer in layers_out}
    for layer in layers_out:
        for base in layer["directBases"]:
            if base in layer_by_id:
                layer_by_id[base]["dependents"].append(layer["id"])
    for layer in layers_out:
        layer["dependents"].sort()

    all_methods_out.sort(key=lambda m: m["name"])
    return layers_out, all_methods_out


def extract_catalogs() -> dict[str, Any]:
    definitions = []
    for name, kind in DEFINITION_KINDS.items():
        if kind.container:
            layout = f"Container block ({kind.container} = {{ id = {{ ... }} }})"
        elif kind.entry_key:
            layout = f"Repeated entry ({kind.entry_key} = {{ {kind.id_field} = ... }})"
        else:
            layout = "Top-level keyed block (id = { ... })"
        definitions.append(
            {
                "name": name,
                "directory": kind.directory,
                "container": kind.container,
                "entryKey": kind.entry_key,
                "idField": kind.id_field,
                "recursive": kind.recursive,
                "defaultFile": kind.default_file,
                "layout": layout,
                "description": kind.description,
            }
        )

    warning_set = set(VALIDATION_WARNING_CODES)
    validation_codes = []
    for code, desc in VALIDATION_CODES.items():
        is_warning = code in warning_set
        validation_codes.append(
            {
                "code": code,
                "description": desc,
                "severity": "warning" if is_warning else "error",
                "suppressible": is_warning,
                "category": classify_validation_code(code, desc),
            }
        )

    effects = [
        {
            "category": cat_name,
            "items": [
                {"label": label, "script": script}
                for label, script in entries
            ],
        }
        for cat_name, entries in EFFECT_CATEGORIES
    ]

    modifiers = [
        {
            "category": cat_name,
            "items": [
                {"label": label, "key": key, "example": example}
                for label, key, example in entries
            ],
        }
        for cat_name, entries in MODIFIER_CATEGORIES
    ]

    return {
        "definitions": definitions,
        "validationCodes": validation_codes,
        "effects": effects,
        "modifiers": modifiers,
        "technologyCategories": list(TECHNOLOGY_CATEGORIES),
    }


def extract_releases() -> list[dict[str, Any]]:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    releases: list[dict[str, Any]] = []
    current_release: dict[str, Any] | None = None
    current_section: dict[str, Any] | None = None
    current_item_lines: list[str] = []

    def flush_item() -> None:
        nonlocal current_item_lines
        if current_section is not None and current_item_lines:
            joined = " ".join(line.strip() for line in current_item_lines).strip()
            if joined:
                current_section["items"].append(joined)
        current_item_lines = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        rel_match = re.match(r"^## \[([^\]]+)\](?:\s*-\s*(.+))?$", line)
        if rel_match:
            flush_item()
            version = rel_match.group(1).strip()
            date = (rel_match.group(2) or "").strip()
            is_unreleased = version.lower() == "unreleased"
            current_release = {
                "version": version,
                "id": "unreleased" if is_unreleased else f"v{version}",
                "date": date or "Upcoming",
                "isUnreleased": is_unreleased,
                "sections": [],
            }
            releases.append(current_release)
            current_section = None
            continue

        sec_match = re.match(r"^### (.+)$", line)
        if sec_match and current_release is not None:
            flush_item()
            current_section = {
                "title": sec_match.group(1).strip(),
                "items": [],
            }
            current_release["sections"].append(current_section)
            continue

        if current_section is not None:
            if re.match(r"^- ", line):
                flush_item()
                current_item_lines = [line[2:]]
            elif line.startswith("  ") and current_item_lines:
                current_item_lines.append(line.strip())
            elif line.strip() == "":
                flush_item()

    flush_item()

    for rel in releases:
        counts = {"added": 0, "changed": 0, "fixed": 0, "removed": 0, "migration": 0, "other": 0}
        total = 0
        for sec in rel["sections"]:
            n = len(sec["items"])
            total += n
            key = sec["title"].lower()
            if "added" in key:
                counts["added"] += n
            elif "changed" in key:
                counts["changed"] += n
            elif "fixed" in key:
                counts["fixed"] += n
            elif "removed" in key:
                counts["removed"] += n
            elif "migration" in key:
                counts["migration"] += n
            else:
                counts["other"] += n
        rel["counts"] = counts
        rel["totalItems"] = total

    return releases


LINK_REWRITES: dict[str, str] = {
    "docs/api.md": "/docs/api",
    "docs/models.md": "/docs/models",
    "docs/recipes.md": "/docs/recipes",
    "docs/catalogs.md": "/docs/catalogs",
    "docs/agent_traps.md": "/docs/agent-traps",
    "docs/internals/parser.md": "/docs/internals-parser",
    "docs/internals/mod_layers.md": "/docs/internals-layers",
    "api.md": "/docs/api",
    "models.md": "/docs/models",
    "recipes.md": "/docs/recipes",
    "catalogs.md": "/docs/catalogs",
    "agent_traps.md": "/docs/agent-traps",
    "internals/parser.md": "/docs/internals-parser",
    "internals/mod_layers.md": "/docs/internals-layers",
    "parser.md": "/docs/internals-parser",
    "mod_layers.md": "/docs/internals-layers",
    "CONTRIBUTING.md": "/docs/contributing",
    "../api.md": "/docs/api",
    "../models.md": "/docs/models",
    "../recipes.md": "/docs/recipes",
    "LICENSE": "https://github.com/Vaspyyy/hoi4-agent-sdk/blob/main/LICENSE",
}


def rewrite_doc_links(md: str) -> str:
    def repl(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        base, _, anchor = target.partition("#")
        if base in LINK_REWRITES:
            new_target = LINK_REWRITES[base]
            if anchor:
                new_target = f"{new_target}#{anchor}"
            return f"[{label}]({new_target})"
        return match.group(0)

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl, md)


def extract_headings(md: str) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    in_code = False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = re.match(r"^(#{2,3})\s+(.+)$", line)
        if not m:
            continue
        level = len(m.group(1))
        raw_title = m.group(2).strip()
        clean_title = re.sub(r"`([^`]*)`", r"\1", raw_title)
        clean_title = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean_title)
        base_slug = slugify(clean_title)
        count = seen.get(base_slug, 0)
        seen[base_slug] = count + 1
        slug = base_slug if count == 0 else f"{base_slug}-{count}"
        headings.append({"level": level, "title": clean_title, "id": slug})
    return headings


def extract_docs() -> list[dict[str, Any]]:
    doc_specs = [
        {
            "slug": "getting-started",
            "title": "Getting Started",
            "group": "Overview",
            "summary": "Installation, extras matrix, quick start, project services, and the transaction safety loop.",
            "source": "README.md",
            "badge": "Start here",
        },
        {
            "slug": "recipes",
            "title": "Common Recipes",
            "group": "Guides",
            "summary": "End-to-end recipes for countries, Wikimedia Commons sourcing, focus trees, events, civil wars, and decision chains.",
            "source": "docs/recipes.md",
            "badge": "11 recipes",
        },
        {
            "slug": "agent-traps",
            "title": "Common Agent Traps",
            "group": "Guides",
            "summary": "Hard-won Hearts of Iron IV scripting pitfalls: faction direction, picture stems, OOB DLC splits, and scope boundaries.",
            "source": "docs/agent_traps.md",
            "badge": "19 traps",
        },
        {
            "slug": "api",
            "title": "API Reference",
            "group": "Reference",
            "summary": "Complete Mod facade documentation covering countries, states, map editing, events, focuses, decisions, OOBs, and validation.",
            "source": "docs/api.md",
            "badge": "Full spec",
        },
        {
            "slug": "models",
            "title": "Data Models",
            "group": "Reference",
            "summary": "Typed dataclasses for Focus, Country, Character, OrderOfBattle, State, Decision, Event, Idea, Ideology, and reports.",
            "source": "docs/models.md",
            "badge": "Models",
        },
        {
            "slug": "catalogs",
            "title": "Effect & Modifier Catalogs",
            "group": "Reference",
            "summary": "HOI4 1.19 curated effects, modifiers, and all 133 valid add_tech_bonus technology categories.",
            "source": "docs/catalogs.md",
            "badge": "HOI4 1.19",
        },
        {
            "slug": "internals-parser",
            "title": "Lossless Parser & Span Patching",
            "group": "Internals",
            "summary": "How hoi4.textio, hoi4.syntax, hoi4.patching, and sync_body achieve byte-exact source preservation.",
            "source": "docs/internals/parser.md",
            "badge": "Core engine",
        },
        {
            "slug": "internals-layers",
            "title": "Mod Layer Architecture",
            "group": "Internals",
            "summary": "How the 30-module acyclic inheritance graph replaces a 12,992-line monolith with mypy-enforced boundaries.",
            "source": "docs/internals/mod_layers.md",
            "badge": "30 layers",
        },
        {
            "slug": "contributing",
            "title": "Contributing & Testing",
            "group": "Project",
            "summary": "Development environment, required checks, serializer invariants, and real-install corpus tests.",
            "source": "CONTRIBUTING.md",
            "badge": "Dev guide",
        },
    ]

    docs_out: list[dict[str, Any]] = []
    for spec in doc_specs:
        src_path = REPO_ROOT / spec["source"]
        raw = src_path.read_text(encoding="utf-8")
        # Strip leading H1 so the page header renders it cleanly without duplication
        body = re.sub(r"^#\s+.+\n+", "", raw, count=1)
        body = rewrite_doc_links(body)
        words = len(re.findall(r"\S+", body))
        headings = extract_headings(body)
        docs_out.append(
            {
                "slug": spec["slug"],
                "title": spec["title"],
                "group": spec["group"],
                "summary": spec["summary"],
                "sourcePath": spec["source"],
                "badge": spec["badge"],
                "lines": len(raw.splitlines()),
                "words": words,
                "readingMinutes": max(1, round(words / 220)),
                "headings": headings,
                "markdown": body,
            }
        )
    return docs_out


def build_search_index(
    docs: list[dict[str, Any]],
    methods: list[dict[str, Any]],
    catalogs: dict[str, Any],
    releases: list[dict[str, Any]],
    layers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for doc in docs:
        items.append(
            {
                "id": f"doc:{doc['slug']}",
                "kind": "Doc",
                "title": doc["title"],
                "subtitle": doc["summary"],
                "href": f"/docs/{doc['slug']}",
                "keywords": f"{doc['group']} {doc['sourcePath']}",
            }
        )
        for h in doc["headings"]:
            if h["level"] == 2:
                items.append(
                    {
                        "id": f"doc:{doc['slug']}#{h['id']}",
                        "kind": "Section",
                        "title": h["title"],
                        "subtitle": f"{doc['title']} · {doc['sourcePath']}",
                        "href": f"/docs/{doc['slug']}#{h['id']}",
                        "keywords": doc["title"],
                    }
                )

    for m in methods:
        items.append(
            {
                "id": f"method:{m['name']}",
                "kind": "Method",
                "title": f"Mod.{m['name']}()",
                "subtitle": m["summary"] or m["signature"],
                "href": f"/reference?tab=methods&q={m['name']}",
                "keywords": f"{m['layer']} {m['layerClass']} {m['signature']}",
            }
        )

    for layer in layers:
        items.append(
            {
                "id": f"layer:{layer['id']}",
                "kind": "Layer",
                "title": f"{layer['className']} ({layer['id']})",
                "subtitle": layer["description"],
                "href": f"/architecture?layer={layer['id']}",
                "keywords": f"{layer['filePath']} {layer['tier']}",
            }
        )

    for d in catalogs["definitions"]:
        items.append(
            {
                "id": f"def:{d['name']}",
                "kind": "Definition",
                "title": d["name"],
                "subtitle": f"{d['directory']} — {d['description']}",
                "href": f"/reference?tab=definitions&q={d['name']}",
                "keywords": f"{d['directory']} {d['container'] or ''} {d['entryKey'] or ''}",
            }
        )

    for v in catalogs["validationCodes"]:
        items.append(
            {
                "id": f"val:{v['code']}",
                "kind": "Validation",
                "title": v["code"],
                "subtitle": f"[{v['severity']}] {v['description']}",
                "href": f"/reference?tab=validation&q={v['code']}",
                "keywords": f"{v['severity']} {v['category']}",
            }
        )

    for rel in releases:
        items.append(
            {
                "id": f"rel:{rel['id']}",
                "kind": "Release",
                "title": f"{rel['version']} ({rel['date']})",
                "subtitle": f"{rel['totalItems']} entries (+{rel['counts']['added']} added, ~{rel['counts']['changed']} changed, !{rel['counts']['fixed']} fixed)",
                "href": f"/releases#{rel['id']}",
                "keywords": "changelog release notes version",
            }
        )

    return items


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    layers, methods = extract_layers()
    catalogs = extract_catalogs()
    releases = extract_releases()
    docs = extract_docs()
    search_index = build_search_index(docs, methods, catalogs, releases, layers)

    test_files = sorted(p.name for p in (REPO_ROOT / "tests").glob("test_*.py"))
    src_files = list((REPO_ROOT / "src" / "hoi4").rglob("*.py"))

    meta = {
        "name": project["name"],
        "version": project["version"],
        "description": project["description"],
        "license": project.get("license", "MIT"),
        "requiresPython": project["requires-python"],
        "repository": project.get("urls", {}).get("Homepage", "https://github.com/Vaspyyy/hoi4-agent-sdk"),
        "issues": project.get("urls", {}).get("Issues", "https://github.com/Vaspyyy/hoi4-agent-sdk/issues"),
        "extras": project.get("optional-dependencies", {}),
        "exports": list(hoi4.__all__),
        "stats": {
            "runtimeDependencies": len(project.get("dependencies", [])),
            "publicExports": len(hoi4.__all__),
            "modMethods": len(methods),
            "layerModules": len(layers),
            "definitionKinds": len(catalogs["definitions"]),
            "validationCodes": len(catalogs["validationCodes"]),
            "warningCodes": sum(1 for v in catalogs["validationCodes"] if v["severity"] == "warning"),
            "errorCodes": sum(1 for v in catalogs["validationCodes"] if v["severity"] == "error"),
            "curatedEffects": sum(len(cat["items"]) for cat in catalogs["effects"]),
            "curatedModifiers": sum(len(cat["items"]) for cat in catalogs["modifiers"]),
            "techCategories": len(catalogs["technologyCategories"]),
            "releases": len(releases),
            "testSuites": len(test_files),
            "sourceModules": len(src_files),
            "docsPages": len(docs),
            "unreleasedEntries": releases[0]["totalItems"] if releases and releases[0]["isUnreleased"] else 0,
        },
    }

    outputs = {
        "meta.json": meta,
        "layers.json": {"layers": layers, "methods": methods},
        "catalogs.json": catalogs,
        "releases.json": releases,
        "docs.json": docs,
        "search-index.json": search_index,
    }

    for filename, payload in outputs.items():
        target = OUT_DIR / filename
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        f"Extracted SDK data v{meta['version']}: "
        f"{meta['stats']['modMethods']} methods across {meta['stats']['layerModules']} layers, "
        f"{meta['stats']['docsPages']} docs, {meta['stats']['releases']} releases, "
        f"{len(search_index)} search entries -> {OUT_DIR}"
    )


if __name__ == "__main__":
    main()
