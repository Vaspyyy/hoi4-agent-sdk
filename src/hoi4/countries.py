"""
Country creation, reading, and serialization.

A Country in HOI4 spans multiple files:
  - common/country_tags/*.txt       (tag registration)
  - common/countries/{TAG}.txt      (color, graphical culture)
  - history/countries/{TAG} -*.txt  (capital, politics, leader)
  - common/characters/{TAG}*.txt    (character definitions)
  - localisation/english/*.yml      (name, adjective)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .tags import (
    add_country_tag,
    remove_country_tag as _remove_country_tag,
    resolve_country_filename,
)
from .patching import replace_assignment, set_block, set_scalar, top_level_assignments
from .paths import require_country_tag, safe_file_stem
from .parser import find_assignment_block
from .script import pdx_string
from .types import Country, Leader

COLOR_RE = re.compile(r"\bcolor\s*=\s*\{\s*(\d+)\s+(\d+)\s+(\d+)\s*\}")
CAPITAL_RE = re.compile(r"\bcapital\s*=\s*(\d+)")
RESEARCH_SLOTS_RE = re.compile(r"\bset_research_slots\s*=\s*(\d+)")
POP_RE = re.compile(r"\b(democratic|fascism|communism|neutrality)\s*=\s*(\d+)")
RULING_PARTY_RE = re.compile(r"\bruling_party\s*=\s*(\w+)")
IDEOLOGY_RE = re.compile(r"\bideology\s*=\s*(\w+)")


def read_country(
    mod_root: Path,
    tag: str,
    hoi4_install: Optional[Path] = None,
    _loc_cache: Optional[dict[str, dict[str, str]]] = None,
    _tag_mappings: Optional[dict[Path, dict[str, str]]] = None,
) -> Country:
    country = Country(tag=tag)

    _read_definition(country, mod_root, hoi4_install, _tag_mappings)
    _read_history(country, mod_root, hoi4_install)
    _read_character(country, mod_root, hoi4_install)

    if _loc_cache is not None:
        _read_loc_from_cache(country, _loc_cache)
    else:
        _read_localisation(country, mod_root, hoi4_install)

    return country


def _read_definition(
    country: Country,
    mod_root: Path,
    hoi4_install: Optional[Path],
    tag_mappings: Optional[dict[Path, dict[str, str]]] = None,
) -> None:
    for base in [mod_root, hoi4_install]:
        if base is None:
            continue
        p = resolve_country_filename(base, country.tag, (tag_mappings or {}).get(base))
        if p and p.exists():
            txt = p.read_text(encoding="utf-8", errors="ignore")
            country.definition_path = p.resolve()
            country.raw_definition = txt
            m = COLOR_RE.search(txt)
            if m:
                country.color = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return


def _read_history(country: Country, mod_root: Path, hoi4_install: Optional[Path]) -> None:
    for base in [mod_root, hoi4_install]:
        if base is None:
            continue
        d = base / "history" / "countries"
        if not d.exists():
            continue
        f = _find_history_file(d, country.tag)
        if not f:
            continue
        txt = f.read_text(encoding="utf-8", errors="ignore")
        country.history_path = f.resolve()
        country.raw_history = txt

        cap = CAPITAL_RE.search(txt)
        if cap:
            country.capital = int(cap.group(1))

        research_slots = RESEARCH_SLOTS_RE.search(txt)
        if research_slots:
            country.research_slots = int(research_slots.group(1))

        pops: dict[str, int] = {}
        for m in POP_RE.finditer(txt):
            pops[m.group(1)] = int(m.group(2))
        if pops:
            country.popularities = pops

        rp = RULING_PARTY_RE.search(txt)
        if rp:
            country.ruling_party = rp.group(1)
            country.elections_allowed = rp.group(1) == "democratic"

        leader_name = _extract_leader_name(txt)
        if leader_name:
            country.leader = Leader(
                name=leader_name,
                character_id=f"{country.tag}_leader_1",
            )

        return


def _read_localisation(country: Country, mod_root: Path, hoi4_install: Optional[Path]) -> None:
    for base in [mod_root, hoi4_install]:
        if base is None:
            continue
        loc_dir = base / "localisation" / "english"
        if not loc_dir.exists():
            continue
        candidates = [
            loc_dir / "countries_l_english.yml",
            loc_dir / "countries_cosmetic_l_english.yml",
        ]
        candidates.extend(f for f in loc_dir.rglob("*.yml") if f not in candidates)
        for f in candidates:
            if not f.exists():
                continue
            raw = f.read_bytes()
            try:
                txt = raw.decode("utf-8-sig")
            except Exception:
                txt = raw.decode("utf-8", errors="ignore")
            if f"{country.tag}:" not in txt:
                continue
            for line in txt.splitlines():
                s = line.strip()
                if s.startswith(f"{country.tag}:"):
                    country.name = s.split(" ", 1)[-1].strip().strip('"')
                if s.startswith(f"{country.tag}_ADJ:"):
                    country.adjective = s.split(" ", 1)[-1].strip().strip('"')
            return


def _build_loc_cache(mod_root: Path) -> dict[str, dict[str, str]]:
    cache: dict[str, dict[str, str]] = {}
    loc_dir = mod_root / "localisation" / "english"
    if not loc_dir.exists():
        return cache
    for f in loc_dir.rglob("*.yml"):
        raw = f.read_bytes()
        try:
            txt = raw.decode("utf-8-sig")
        except Exception:
            txt = raw.decode("utf-8", errors="ignore")
        for line in txt.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("l_"):
                continue
            m = re.match(r"^\s*([A-Z0-9]{3}[A-Z0-9_]*):", s)
            if m:
                tag_key = m.group(1)
                tag = tag_key[:3] if len(tag_key) >= 3 and tag_key[:3].isupper() else None
                if tag and tag not in cache:
                    cache[tag] = {}
                if tag:
                    val = s.split(" ", 1)[-1].strip().strip('"')
                    cache[tag][tag_key] = val
    return cache


def _read_loc_from_cache(country: Country, cache: dict[str, dict[str, str]]) -> None:
    entries = cache.get(country.tag)
    if not entries:
        return
    for key, val in entries.items():
        if key == country.tag or key == f"{country.tag}:0":
            country.name = val
        elif key.startswith(f"{country.tag}_ADJ"):
            country.adjective = val


def _read_character(country: Country, mod_root: Path, hoi4_install: Optional[Path]) -> None:
    for base in [mod_root, hoi4_install]:
        if base is None:
            continue
        p = base / f"common/characters/{country.tag}_characters.txt"
        if not p.exists():
            p = base / f"common/characters/{country.tag}.txt"
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        country.character_path = p.resolve()
        country.raw_character = txt

        ideology_m = IDEOLOGY_RE.search(txt)
        name_m = re.search(r'name\s*=\s*"([^"]*)"', txt)

        if country.leader is None:
            country.leader = Leader(
                name=name_m.group(1) if name_m else "",
                character_id=f"{country.tag}_leader_1",
                ideology=ideology_m.group(1) if ideology_m else "liberalism",
            )
        else:
            if ideology_m:
                country.leader.ideology = ideology_m.group(1)
            if name_m:
                country.leader.name = name_m.group(1)
        return


def _find_history_file(history_dir: Path, tag: str) -> Optional[Path]:
    for f in history_dir.glob(f"{tag} - *.txt"):
        return f
    for f in history_dir.glob(f"{tag}*.txt"):
        return f
    return None


def _extract_leader_name(txt: str) -> Optional[str]:
    lines = txt.splitlines()
    for idx, line in enumerate(lines):
        if "create_country_leader" in line or "recruit_character" in line:
            for j in range(idx + 1, min(idx + 12, len(lines))):
                name_match = re.search(r'name\s*=\s*"([^"]*)"', lines[j])
                if name_match:
                    return name_match.group(1)
    return None


def write_country_tag(mod_root: Path, tag: str) -> Path:
    return add_country_tag(mod_root, tag)


def remove_country_tag(mod_root: Path, tag: str) -> Path | None:
    return _remove_country_tag(mod_root, tag)


def write_country_definition(mod_root: Path, country: Country) -> None:
    p = country.definition_path or mod_root / f"common/countries/{country.tag}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_country_files(mod_root, country)[p]
    p.write_text(content, encoding="utf-8")


def write_country_history(mod_root: Path, country: Country) -> None:
    tag = country.tag
    safe_name = safe_file_stem(country.name or tag, fallback=tag)
    p = (
        mod_root / f"history/countries/{tag} - {safe_name}.txt"
        if "name" in country.touched_fields or country.history_path is None
        else country.history_path
    )
    p.parent.mkdir(parents=True, exist_ok=True)

    for old in p.parent.glob(f"{tag} - *.txt"):
        if old != p:
            old.unlink()

    serialized = serialize_country_files(mod_root, country)
    p.write_text(serialized[p], encoding="utf-8")


def write_country_localisation(mod_root: Path, country: Country) -> None:
    loc = mod_root / f"localisation/english/{country.tag}_country_l_english.yml"
    loc.parent.mkdir(parents=True, exist_ok=True)
    lines = ["l_english:"]
    name = country.name or country.tag
    adj = country.adjective or name
    for suffix in ["", "_neutrality", "_democratic", "_fascism", "_communism"]:
        lines.append(f" {country.tag}{suffix}:0 {pdx_string(name)}")
        lines.append(f" {country.tag}{suffix}_DEF:0 {pdx_string(name)}")
    lines.append(f" {country.tag}_ADJ:0 {pdx_string(adj)}")
    lines.append("")
    loc.write_text("\n".join(lines), encoding="utf-8-sig")


def write_character_file(mod_root: Path, country: Country) -> None:
    if not country.leader:
        return
    serialized = serialize_country_files(mod_root, country)
    tag = country.tag
    p = country.character_path or mod_root / f"common/characters/{tag}_characters.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(serialized[p], encoding="utf-8")


def write_all_country_files(mod_root: Path, country: Country) -> list[Path]:
    written = [write_country_tag(mod_root, country.tag)]
    write_country_definition(mod_root, country)
    written.append(country.definition_path or mod_root / f"common/countries/{country.tag}.txt")
    write_country_history(mod_root, country)
    safe_name = safe_file_stem(country.name or country.tag, fallback=country.tag)
    written.append(
        mod_root / f"history/countries/{country.tag} - {safe_name}.txt"
        if "name" in country.touched_fields or country.history_path is None
        else country.history_path
    )
    write_character_file(mod_root, country)
    if country.leader:
        written.append(
            country.character_path or mod_root / f"common/characters/{country.tag}_characters.txt"
        )
    write_country_localisation(mod_root, country)
    written.append(mod_root / "localisation" / "english" / f"{country.tag}_country_l_english.yml")
    return written


def country_file_paths(mod_root: Path, country: Country) -> list[Path]:
    tag = country.tag
    safe_name = safe_file_stem(country.name or tag, fallback=tag)
    paths = [
        country.definition_path or mod_root / f"common/countries/{tag}.txt",
        country.history_path or mod_root / f"history/countries/{tag} - {safe_name}.txt",
    ]
    if country.leader:
        paths.append(country.character_path or mod_root / f"common/characters/{tag}_characters.txt")
    return paths


def serialize_country_files(mod_root: Path, country: Country) -> dict[Path, str]:
    tag = require_country_tag(country.tag)
    files: dict[Path, str] = {}

    def_path = country.definition_path or mod_root / f"common/countries/{tag}.txt"
    try:
        r, g, b = country.color
    except (TypeError, ValueError):
        r, g, b = 128, 128, 128
    definition = country.raw_definition
    if not definition:
        definition = (
            f"graphical_culture = {country.graphical_culture}\n"
            f"graphical_culture_2d = {country.graphical_culture_2d}\n"
            f"color = {{ {r} {g} {b} }}\n"
        )
    else:
        if "*" in country.touched_fields or "graphical_culture" in country.touched_fields:
            definition = set_scalar(definition, "graphical_culture", country.graphical_culture)
        if "*" in country.touched_fields or "graphical_culture_2d" in country.touched_fields:
            definition = set_scalar(
                definition, "graphical_culture_2d", country.graphical_culture_2d
            )
        if "*" in country.touched_fields or "color" in country.touched_fields:
            definition = set_block(definition, "color", f"{r} {g} {b}")
    files[def_path] = definition

    safe_name = safe_file_stem(country.name or tag, fallback=tag)
    hist_path = (
        mod_root / f"history/countries/{tag} - {safe_name}.txt"
        if "name" in country.touched_fields or country.history_path is None
        else country.history_path
    )
    leader = country.leader or Leader(name="Leader", character_id=f"{tag}_leader_1")
    elections = "yes" if country.elections_allowed else "no"
    pops = country.popularities
    ideas_block = ""
    if country.ideas:
        ideas_lines = "\n".join(f" {idea}" for idea in country.ideas)
        ideas_block = f"\nadd_ideas = {{\n{ideas_lines}\n}}\n"
    research_slots = (
        f"set_research_slots = {country.research_slots}\n\n"
        if country.research_slots is not None
        else ""
    )
    generated_history = (
        f"capital = {country.capital}\n"
        f"\n"
        f"{research_slots}"
        f"recruit_character = {leader.character_id}\n"
        f"\n"
        f"set_popularities = {{\n"
        f" democratic = {pops.get('democratic', 0)}\n"
        f" fascism = {pops.get('fascism', 0)}\n"
        f" communism = {pops.get('communism', 0)}\n"
        f" neutrality = {pops.get('neutrality', 0)}\n"
        f"}}\n"
        f"\n"
        f"set_politics = {{\n"
        f" ruling_party = {country.ruling_party}\n"
        f' last_election = "1936.1.1"\n'
        f" elections_allowed = {elections}\n"
        f"}}\n"
        f"\n"
        f"{ideas_block}"
    )
    history = country.raw_history
    if not history:
        history = generated_history
    else:
        touched = country.touched_fields
        if "*" in touched or "capital" in touched:
            history = set_scalar(history, "capital", str(country.capital))
        if "*" in touched or "research_slots" in touched:
            history = set_scalar(
                history,
                "set_research_slots",
                None if country.research_slots is None else str(country.research_slots),
            )
        if "*" in touched or "popularities" in touched:
            popularity_body = "\n".join(
                f"{key} = {pops.get(key, 0)}"
                for key in ("democratic", "fascism", "communism", "neutrality")
            )
            history = set_block(history, "set_popularities", popularity_body)
        if "*" in touched or {"ruling_party", "elections_allowed"} & touched:
            politics_body = "\n".join(
                [
                    f"ruling_party = {country.ruling_party}",
                    'last_election = "1936.1.1"',
                    f"elections_allowed = {elections}",
                ]
            )
            history = set_block(history, "set_politics", politics_body)
        if "*" in touched or "ideas" in touched:
            history = set_block(
                history, "add_ideas", "\n".join(country.ideas) if country.ideas else None
            )
    files[hist_path] = history

    if country.leader:
        char_path = country.character_path or mod_root / f"common/characters/{tag}_characters.txt"
        ld = country.leader
        generated_character = (
            f"characters = {{\n"
            f" {ld.character_id} = {{\n"
            f"  name = {pdx_string(ld.name)}\n"
            f"\n"
            f"  roles = {{ country_leader }}\n"
            f"\n"
            f"  portraits = {{\n"
            f"   civilian = {{\n"
            f"    large = GFX_portrait_{tag}_{ld.portrait_slug or ld.character_id}\n"
            f"   }}\n"
            f"  }}\n"
            f"\n"
            f"  country_leader = {{\n"
            f"   ideology = {ld.ideology}\n"
            f"   desc = {ld.character_id}_desc\n"
            f'   expire = "1965.1.1"\n'
            f"   traits = {{ }}\n"
            f"  }}\n"
            f" }}\n"
            f"}}\n"
        )
        # Existing character files may define advisors/generals alongside the leader.
        # Preserve them byte-for-byte unless a leader field was explicitly changed.
        character = country.raw_character
        if character and any(
            field.startswith("leader_") or field == "*" for field in country.touched_fields
        ):
            character = _patch_character(character, ld)
        files[char_path] = character or generated_character

    return files


def _patch_character(text: str, leader: Leader) -> str:
    root = find_assignment_block(text, "characters")
    if root is None:
        return text
    body = root[0]
    leader_span = next(
        (
            span
            for span in top_level_assignments(body)
            if span.key == leader.character_id
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ),
        None,
    )
    if leader_span is None or leader_span.body_start is None or leader_span.body_end is None:
        return text
    leader_body = body[leader_span.body_start : leader_span.body_end]
    leader_body = set_scalar(leader_body, "name", pdx_string(leader.name))
    role = next(
        (
            span
            for span in top_level_assignments(leader_body)
            if span.key == "country_leader"
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ),
        None,
    )
    if role is not None and role.body_start is not None and role.body_end is not None:
        role_body = leader_body[role.body_start : role.body_end]
        role_body = set_scalar(role_body, "ideology", leader.ideology)
        leader_body = replace_assignment(
            leader_body,
            role,
            "country_leader = {\n"
            + "\n".join("\t" + line for line in role_body.strip().splitlines())
            + "\n}",
        )
    body = replace_assignment(
        body,
        leader_span,
        f"{leader.character_id} = {{\n"
        + "\n".join("\t" + line for line in leader_body.strip().splitlines())
        + "\n}",
    )
    replacement = (
        "characters = {\n" + "\n".join("\t" + line for line in body.strip().splitlines()) + "\n}"
    )
    return text[: root[1]] + replacement + text[root[2] :]
