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
from .patching import (
    replace_assignment,
    replace_assignment_body,
    set_block,
    set_scalar,
    top_level_assignments,
)
from .paths import require_country_tag, safe_file_stem
from .parser import find_assignment_block
from .script import pdx_string, pdx_value
from .structured_patching import patch_scalar_mapping
from .types import Country, Leader

COLOR_RE = re.compile(
    r"\bcolor\s*=\s*(?:rgb\s*)?\{\s*(\d+)\s+(\d+)\s+(\d+)\s*\}"
)
COLOR_FIELD_RE = re.compile(
    r"(\bcolor(?:_ui)?\s*=\s*(?:rgb\s*)?\{\s*)\d+\s+\d+\s+\d+(\s*\})"
)
CAPITAL_RE = re.compile(r"\bcapital\s*=\s*(\d+)")
RESEARCH_SLOTS_RE = re.compile(r"\bset_research_slots\s*=\s*(\d+)")
STANDARD_IDEOLOGY_GROUPS = ("democratic", "fascism", "communism", "neutrality")


def read_country(
    mod_root: Path,
    tag: str,
    hoi4_install: Optional[Path] = None,
    _loc_cache: Optional[dict[str, dict[str, str]]] = None,
    _tag_mappings: Optional[dict[Path, dict[str, str]]] = None,
) -> Country:
    country = Country(tag=tag)

    _read_definition(country, mod_root, hoi4_install, _tag_mappings)
    _read_color(country, mod_root, hoi4_install, _tag_mappings)
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


def _read_color(
    country: Country,
    mod_root: Path,
    hoi4_install: Optional[Path],
    tag_mappings: Optional[dict[Path, dict[str, str]]] = None,
) -> None:
    """Resolve country colour using the same vanilla-then-mod precedence as HOI4."""

    resolved: tuple[int, int, int] | None = None
    for base in (hoi4_install, mod_root):
        if base is None:
            continue
        definition = resolve_country_filename(base, country.tag, (tag_mappings or {}).get(base))
        if definition is not None and definition.is_file():
            match = COLOR_RE.search(definition.read_text(encoding="utf-8", errors="ignore"))
            if match:
                resolved = tuple(int(part) for part in match.groups())  # type: ignore[assignment]
        colors_path = base / "common" / "countries" / "colors.txt"
        if not colors_path.is_file():
            continue
        text = colors_path.read_text(encoding="utf-8", errors="ignore")
        span = next((item for item in top_level_assignments(text) if item.key == country.tag), None)
        if span is None or not span.is_block or span.body_start is None or span.body_end is None:
            continue
        match = COLOR_RE.search(text[span.body_start : span.body_end])
        if match:
            resolved = tuple(int(part) for part in match.groups())  # type: ignore[assignment]
    if resolved is not None:
        country.color = resolved


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

        assignments = {span.key: span for span in top_level_assignments(txt)}
        for key, field_name in (
            ("set_stability", "stability"),
            ("set_war_support", "war_support"),
        ):
            span = assignments.get(key)
            if span is not None and not span.is_block:
                setattr(
                    country,
                    field_name,
                    _coerce_history_scalar(txt[span.value_start : span.value_end]),
                )
        oob_span = assignments.get("oob")
        if oob_span is not None and not oob_span.is_block:
            country.oob = _unquote_history_scalar(
                txt[oob_span.value_start : oob_span.value_end]
            )
        technology_span = assignments.get("set_technology")
        if (
            technology_span is not None
            and technology_span.is_block
            and technology_span.body_start is not None
            and technology_span.body_end is not None
        ):
            technology_body = txt[
                technology_span.body_start : technology_span.body_end
            ]
            country.technologies = {
                span.key: int(value)
                for span in top_level_assignments(technology_body)
                if not span.is_block
                and (
                    value := technology_body[
                        span.value_start : span.value_end
                    ].strip()
                ).lstrip("-").isdigit()
            }

        pops = _read_popularities(txt)
        if pops:
            country.popularities = pops

        ruling_party, elections_allowed = _read_politics(txt)
        if ruling_party:
            country.ruling_party = ruling_party
            country.elections_allowed = (
                ruling_party == "democratic"
                if elections_allowed is None
                else elections_allowed
            )

        country.ideas = _read_assigned_ideas(txt)

        leader_name = _extract_leader_name(txt)
        recruited_ids = _read_recruited_character_ids(txt)
        if recruited_ids:
            country.leader = Leader(
                name=leader_name or "",
                character_id=recruited_ids[0],
                portrait_slug=recruited_ids[0].removeprefix(f"{country.tag}_"),
            )
        elif leader_name:
            country.leader = Leader(
                name=leader_name,
                character_id=f"{country.tag}_leader_1",
                portrait_slug="leader_1",
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

        definitions = _character_definitions(txt)
        recruited = _read_recruited_character_ids(country.raw_history)
        by_id = {character_id: body for character_id, body in definitions}
        leader_ids = [
            character_id
            for character_id, body in definitions
            if find_assignment_block(body, "country_leader") is not None
        ]
        selected_id = next((item for item in recruited if item in leader_ids), "")
        if not selected_id and country.leader and country.leader.character_id in by_id:
            selected_id = country.leader.character_id
        if not selected_id and leader_ids:
            selected_id = leader_ids[0]
        if not selected_id:
            selected_id = next((item for item in recruited if item in by_id), "")
        if not selected_id:
            return

        character_body = by_id[selected_id]
        role = find_assignment_block(character_body, "country_leader")
        role_body = role[0] if role is not None else ""
        name = _direct_scalar(character_body, "name")
        ideology = _direct_scalar(role_body, "ideology") or "liberalism"
        portrait_match = re.search(
            rf"\blarge\s*=\s*GFX_portrait_{re.escape(country.tag)}_([A-Za-z0-9_.:-]+)",
            character_body,
        )
        portrait_slug = (
            portrait_match.group(1)
            if portrait_match
            else selected_id.removeprefix(f"{country.tag}_")
        )
        country.leader = Leader(
            name=name or (country.leader.name if country.leader else "") or selected_id,
            character_id=selected_id,
            ideology=ideology,
            portrait_slug=portrait_slug,
        )
        return


def _read_assigned_ideas(history: str) -> list[str]:
    assigned: list[str] = []
    removed: set[str] = set()
    identifier = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]*")
    for span in top_level_assignments(history):
        if span.key not in {"add_ideas", "remove_ideas"}:
            continue
        if span.is_block and span.body_start is not None and span.body_end is not None:
            value = history[span.body_start : span.body_end]
        else:
            value = history[span.value_start : span.value_end]
        values = identifier.findall(re.sub(r"#.*", "", value))
        if span.key == "add_ideas":
            assigned.extend(values)
        else:
            removed.update(values)
    return [idea for idea in dict.fromkeys(assigned) if idea not in removed]


def _direct_scalar(text: str, key: str) -> str:
    for span in top_level_assignments(text):
        if span.key == key and not span.is_block:
            return text[span.value_start : span.value_end].strip().strip('"')
    return ""


def _read_recruited_character_ids(history: str) -> list[str]:
    character_ids: list[str] = []
    for span in top_level_assignments(history):
        if span.key == "recruit_character" and not span.is_block:
            value = history[span.value_start : span.value_end].strip().strip('"')
            if value and value not in character_ids:
                character_ids.append(value)
        elif (
            span.key == "set_country_leader"
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ):
            value = _direct_scalar(history[span.body_start : span.body_end], "character")
            if value and value not in character_ids:
                character_ids.append(value)
    return character_ids


def _character_definitions(text: str) -> list[tuple[str, str]]:
    wrapper = find_assignment_block(text, "characters")
    body = wrapper[0] if wrapper is not None else text
    return [
        (span.key, body[span.body_start : span.body_end])
        for span in top_level_assignments(body)
        if span.is_block and span.body_start is not None and span.body_end is not None
    ]


def _read_popularities(history: str) -> dict[str, int]:
    """Read every direct ideology group in ``set_popularities``.

    Party groups are data-driven in HOI4. Restricting this block to the four
    vanilla identifiers would silently discard custom ideologies when a
    country is edited and saved.
    """

    popularity = next(
        (
            span
            for span in top_level_assignments(history)
            if span.key == "set_popularities"
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ),
        None,
    )
    if popularity is None or popularity.body_start is None or popularity.body_end is None:
        return {}
    body = history[popularity.body_start : popularity.body_end]
    values: dict[str, int] = {}
    for assignment in top_level_assignments(body):
        if assignment.is_block:
            continue
        raw_value = body[assignment.value_start : assignment.value_end].strip().strip('"')
        try:
            values[assignment.key] = int(raw_value)
        except ValueError:
            continue
    return values


def _read_politics(history: str) -> tuple[str, bool | None]:
    politics = next(
        (
            span
            for span in top_level_assignments(history)
            if span.key == "set_politics"
            and span.is_block
            and span.body_start is not None
            and span.body_end is not None
        ),
        None,
    )
    if politics is None or politics.body_start is None or politics.body_end is None:
        return "", None
    body = history[politics.body_start : politics.body_end]
    assignments = {
        assignment.key: body[assignment.value_start : assignment.value_end]
        for assignment in top_level_assignments(body)
        if not assignment.is_block
    }
    ruling_party = assignments.get("ruling_party", "").strip().strip('"')
    election_value = assignments.get("elections_allowed")
    if election_value is None:
        return ruling_party, None
    return ruling_party, election_value.strip().strip('"').lower() in {"yes", "true", "1"}


def _popularity_body(popularities: dict[str, int]) -> str:
    ordered = [key for key in STANDARD_IDEOLOGY_GROUPS if key in popularities]
    ordered.extend(sorted(set(popularities) - set(ordered)))
    return "\n".join(f"{key} = {popularities[key]}" for key in ordered)


def _country_localisation_suffixes(country: Country) -> list[str]:
    groups = list(STANDARD_IDEOLOGY_GROUPS)
    for group in (*country.popularities, country.ruling_party):
        if group and group not in groups:
            groups.append(group)
    return [""] + [f"_{group}" for group in groups]


def serialize_country_colors_file(
    original: str,
    colors: dict[str, tuple[int, int, int]],
    *,
    deleted_tags: set[str] | None = None,
) -> str:
    """Patch country map/UI colours while retaining unrelated source text."""

    text = original
    for tag in sorted(deleted_tags or set()):
        span = next((item for item in top_level_assignments(text) if item.key == tag), None)
        if span is not None:
            text = replace_assignment(text, span, None)
    for tag, color in sorted(colors.items()):
        r, g, b = color
        if any(channel < 0 or channel > 255 for channel in color):
            raise ValueError(f"Country color for {tag} must use channels from 0 to 255")
        span = next((item for item in top_level_assignments(text) if item.key == tag), None)
        if span is None:
            text = text.rstrip() + ("\n" if text.strip() else "")
            text += (
                f"{tag} = {{\n"
                f"    color = rgb {{ {r} {g} {b} }}\n"
                f"    color_ui = rgb {{ {r} {g} {b} }}\n"
                "}\n"
            )
            continue
        if not span.is_block or span.body_start is None or span.body_end is None:
            replacement = (
                f"{tag} = {{\n"
                f"    color = rgb {{ {r} {g} {b} }}\n"
                f"    color_ui = rgb {{ {r} {g} {b} }}\n"
                "}"
            )
            text = replace_assignment(text, span, replacement)
            continue
        body = text[span.body_start : span.body_end]
        seen: set[str] = set()

        def replace_color(match: re.Match[str]) -> str:
            key_match = re.search(r"color(?:_ui)?", match.group(1))
            if key_match is not None:
                seen.add(key_match.group(0))
            return f"{match.group(1)}{r} {g} {b}{match.group(2)}"

        body = COLOR_FIELD_RE.sub(replace_color, body)
        for key in ("color", "color_ui"):
            if key not in seen:
                body = body.rstrip() + f"\n    {key} = rgb {{ {r} {g} {b} }}\n"
        text = text[: span.body_start] + body + text[span.body_end :]
    return text


def country_color_tags(text: str) -> set[str]:
    """Return country tags defined by a ``common/countries/colors.txt`` source."""

    return {
        span.key
        for span in top_level_assignments(text)
        if span.is_block and re.fullmatch(r"[A-Z0-9]{3}", span.key)
    }


def seed_country_colors_file(original: str, vanilla: str) -> str:
    """Append every vanilla country color entry missing from a mod file."""

    text = original
    existing = country_color_tags(text)
    for span in top_level_assignments(vanilla):
        if (
            not span.is_block
            or span.key in existing
            or not re.fullmatch(r"[A-Z0-9]{3}", span.key)
        ):
            continue
        assignment = vanilla[span.start : span.end].strip()
        text = text.rstrip() + ("\n\n" if text.strip() else "") + assignment + "\n"
        existing.add(span.key)
    return text


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
        if {"*", "name"} & country.touched_fields or country.history_path is None
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
    for suffix in _country_localisation_suffixes(country):
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
        if {"*", "name"} & country.touched_fields or country.history_path is None
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
        if {"*", "name"} & country.touched_fields or country.history_path is None
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
    stability = (
        f"set_stability = {pdx_value(country.stability)}\n"
        if country.stability is not None
        else ""
    )
    war_support = (
        f"set_war_support = {pdx_value(country.war_support)}\n"
        if country.war_support is not None
        else ""
    )
    oob = f"oob = {pdx_string(country.oob)}\n" if country.oob else ""
    technologies = ""
    if country.technologies:
        technology_lines = "\n".join(
            f" {technology} = {level}"
            for technology, level in country.technologies.items()
        )
        technologies = f"set_technology = {{\n{technology_lines}\n}}\n"
    popularity_lines = "\n".join(
        f" {line}" for line in _popularity_body(pops).splitlines()
    )
    generated_history = (
        f"capital = {country.capital}\n"
        f"\n"
        f"{oob}"
        f"{stability}"
        f"{war_support}"
        f"{technologies}"
        f"\n"
        f"{research_slots}"
        f"recruit_character = {leader.character_id}\n"
        f"\n"
        f"set_popularities = {{\n"
        f"{popularity_lines}\n"
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
        if "*" in touched or "stability" in touched:
            history = set_scalar(
                history,
                "set_stability",
                None if country.stability is None else pdx_value(country.stability),
            )
        if "*" in touched or "war_support" in touched:
            history = set_scalar(
                history,
                "set_war_support",
                None if country.war_support is None else pdx_value(country.war_support),
            )
        if "*" in touched or "oob" in touched:
            history = set_scalar(
                history,
                "oob",
                pdx_string(country.oob) if country.oob else None,
            )
        if "*" in touched or "technologies" in touched:
            history = _set_scalar_mapping_block(
                history,
                "set_technology",
                country.technologies,
            )
        if "*" in touched or "popularities" in touched:
            history = set_block(history, "set_popularities", _popularity_body(pops))
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
            history = set_block(history, "remove_ideas", None)
    files[hist_path] = history

    if country.leader:
        char_path = country.character_path or mod_root / f"common/characters/{tag}_characters.txt"
        ld = country.leader
        portrait_key = ld.portrait_slug or ld.character_id.removeprefix(f"{tag}_")
        generated_character = (
            f"characters = {{\n"
            f" {ld.character_id} = {{\n"
            f"  name = {pdx_string(ld.name)}\n"
            f"\n"
            f"  portraits = {{\n"
            f"   civilian = {{\n"
            f"    large = GFX_portrait_{tag}_{portrait_key}\n"
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
            character = _patch_character(character, ld, country.touched_fields, tag)
        files[char_path] = character or generated_character

    return files


def _set_scalar_mapping_block(
    text: str,
    key: str,
    values: dict[str, int],
) -> str:
    spans = [span for span in top_level_assignments(text) if span.key == key]
    if not values:
        return set_block(text, key, None)
    rendered = "\n".join(f"{name} = {level}" for name, level in values.items())
    if not spans:
        return set_block(text, key, rendered)
    span = spans[0]
    if not span.is_block or span.body_start is None or span.body_end is None:
        return set_block(text, key, rendered)
    current = text[span.body_start : span.body_end]
    patched = patch_scalar_mapping(current, values)
    return replace_assignment_body(text, span, patched)


def _unquote_history_scalar(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return text


def _coerce_history_scalar(value: str) -> str | int | float:
    text = _unquote_history_scalar(value)
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def _patch_character(
    text: str,
    leader: Leader,
    touched_fields: set[str],
    tag: str,
) -> str:
    root = find_assignment_block(text, "characters")
    body = root[0] if root is not None else text
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
        raise ValueError(
            f"Cannot update leader '{leader.character_id}': matching character block not found"
        )
    leader_body = body[leader_span.body_start : leader_span.body_end]
    # Migrate the invalid top-level field emitted by SDK 0.4.0/0.4.1. HOI4
    # derives roles from the direct country_leader/advisor/commander blocks.
    leader_body = set_block(leader_body, "roles", None)
    if "*" in touched_fields or "leader_name" in touched_fields:
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
    if "*" in touched_fields or "leader_ideology" in touched_fields:
        if role is None or role.body_start is None or role.body_end is None:
            raise ValueError(
                f"Cannot update leader '{leader.character_id}' ideology: "
                "country_leader block not found"
            )
        role_body = leader_body[role.body_start : role.body_end]
        role_body = set_scalar(role_body, "ideology", leader.ideology)
        leader_body = replace_assignment_body(leader_body, role, role_body)
    if "*" in touched_fields or "leader_portrait_slug" in touched_fields:
        leader_body = _patch_character_portrait(leader_body, tag, leader.portrait_slug)
    body = replace_assignment_body(body, leader_span, leader_body)
    if root is None:
        return body
    open_brace = text.find("{", root[1], root[2])
    if open_brace < 0:
        raise ValueError("Malformed characters block")
    return text[: open_brace + 1] + body + text[root[2] - 1 :]


def _patch_character_portrait(body: str, tag: str, portrait_slug: str) -> str:
    portrait_key = f"GFX_portrait_{tag}_{portrait_slug}"
    portraits = next(
        (span for span in top_level_assignments(body) if span.key == "portraits"),
        None,
    )
    if (
        portraits is None
        or not portraits.is_block
        or portraits.body_start is None
        or portraits.body_end is None
    ):
        return set_block(body, "portraits", f"civilian = {{ large = {portrait_key} }}")
    portraits_body = body[portraits.body_start : portraits.body_end]
    civilian = next(
        (span for span in top_level_assignments(portraits_body) if span.key == "civilian"),
        None,
    )
    if (
        civilian is None
        or not civilian.is_block
        or civilian.body_start is None
        or civilian.body_end is None
    ):
        portraits_body = set_block(
            portraits_body,
            "civilian",
            f"large = {portrait_key}",
        )
    else:
        civilian_body = portraits_body[civilian.body_start : civilian.body_end]
        civilian_body = set_scalar(civilian_body, "large", portrait_key)
        portraits_body = replace_assignment_body(
            portraits_body,
            civilian,
            civilian_body,
        )
    return replace_assignment_body(body, portraits, portraits_body)
