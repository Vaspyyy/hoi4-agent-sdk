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

import colorsys
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .characters import _string_like
from .layers import ContentView, as_view
from .patching import (
    AssignmentSpan,
    append_assignment,
    replace_assignment,
    replace_assignment_body,
    set_block,
    set_scalar,
    top_level_assignments,
)
from .paths import require_country_tag, safe_file_stem
from .script import pdx_string, pdx_value
from .structured_patching import patch_scalar_mapping
from .syntax import (
    Assignment,
    Block,
    Scalar,
    Tagged,
    Value,
    parse,
    quote,
    require_entry,
    require_key,
)
from .tags import (
    add_country_tag,
    resolve_country_filename,
    tag_file_mapping,
)
from .tags import (
    remove_country_tag as _remove_country_tag,
)
from .textio import decode_text, read_text, write_text
from .types import Country, Leader

STANDARD_IDEOLOGY_GROUPS = ("democratic", "fascism", "communism", "neutrality")
_COUNTRY_TAG_KEY_RE = re.compile(r"[A-Z0-9]{3}")

RGB = tuple[int, int, int]


# ── Colours ─────────────────────────────────────────────────────
#
# HOI4 spells a colour three ways: ``{ 57 113 228 }``, ``rgb { 57 113 228 }``
# and ``hsv { 0.6 0.75 0.9 }`` (any case; ``hsv360`` takes degrees and
# percentages). The model always holds RGB channels from 0 to 255.


def _number(scalar: Scalar) -> float | None:
    try:
        return float(Decimal(scalar.value))
    except (InvalidOperation, ValueError):
        return None


def color_from_value(value: Value) -> RGB | None:
    """Return the RGB channels of a colour value, or ``None`` if it is not one."""

    tag = ""
    if type(value) is Tagged:
        tag = value.tag.value.lower()
        block = value.block
    elif type(value) is Block:
        block = value
    else:
        return None
    if any(type(item) is not Scalar for item in block.items):
        return None
    numbers = [_number(item) for item in block.values()]
    if len(numbers) != 3 or any(number is None for number in numbers):
        return None
    first, second, third = (float(number) for number in numbers if number is not None)
    if tag in {"hsv", "hsv360"}:
        if tag == "hsv360":
            first, second, third = first / 360, second / 100, third / 100
        channels = colorsys.hsv_to_rgb(
            first % 1.0 if first > 1.0 else max(first, 0.0),
            min(max(second, 0.0), 1.0),
            min(max(third, 0.0), 1.0),
        )
        return tuple(round(channel * 255) for channel in channels)  # type: ignore[return-value]
    return tuple(min(max(round(channel), 0), 255) for channel in (first, second, third))  # type: ignore[return-value]


def read_color(block: Block, key: str = "color") -> RGB | None:
    """Return the colour of the first direct ``key = ...`` of ``block``."""

    for assignment in block.assignments(key):
        if assignment.op == "=":
            return color_from_value(assignment.value)
    return None


def set_color(text: str, key: str, color: RGB, *, default_tag: str = "") -> str:
    """Set the direct ``key`` colour of ``text`` and keep its spelling.

    A value that already means ``color`` is left byte for byte, even when it
    is written as ``hsv``. Otherwise only the three channel numbers change, so
    ``rgb``/plain style, spacing and comments stay. An ``hsv`` value becomes
    ``rgb``, because the model holds RGB. A missing key is appended as
    ``key = <default_tag> { r g b }``.
    """

    for channel in color:
        if not isinstance(channel, int) or isinstance(channel, bool) or not 0 <= channel <= 255:
            raise ValueError(f"Colour {color!r} must use integer channels from 0 to 255")
    r, g, b = color
    doc = parse(text)
    assignment = next(
        (item for item in doc.root.assignments(key) if item.op == "="), None
    )
    prefix = f"{default_tag} " if default_tag else ""
    if assignment is None:
        return append_assignment(text, f"{key} = {prefix}{{ {r} {g} {b} }}")
    value = assignment.value
    if color_from_value(value) == tuple(color):
        return text
    block = value.block if type(value) is Tagged else value if type(value) is Block else None
    if block is None or len(block.items) != 3 or len(block.values()) != 3:
        replacement = f"{prefix}{{ {r} {g} {b} }}"
        return text[: value.start] + replacement + text[value.end :]
    result = text
    for scalar, channel in reversed(list(zip(block.values(), (r, g, b), strict=True))):
        result = result[: scalar.start] + str(channel) + result[scalar.end :]
    if type(value) is Tagged and value.tag.value.lower() != "rgb":
        result = result[: value.tag.start] + "rgb" + result[value.tag.end :]
    return result


def _root(text: str) -> Block:
    return parse(text).root


def _first_block(block: Block, key: str) -> Block | None:
    for assignment in block.assignments(key):
        if assignment.op == "=" and type(assignment.value) is Block:
            return assignment.value
    return None


def _last_scalar(block: Block, key: str) -> str | None:
    found: str | None = None
    for assignment in block.assignments(key):
        if assignment.op == "=" and type(assignment.value) is Scalar:
            found = assignment.value.value
    return found


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        try:
            number = Decimal(value)
        except InvalidOperation:
            return None
        return int(number) if number == number.to_integral_value() else None


def read_country(
    mod_root: Path,
    tag: str,
    hoi4_install: Path | ContentView | None = None,
    _loc_cache: dict[str, dict[str, str]] | None = None,
    _tag_mappings: dict[ContentView, dict[str, str]] | None = None,
    _color_sources: dict[Path, str | None] | None = None,
) -> Country:
    """Read a country from the mod, falling back to ``hoi4_install``.

    ``hoi4_install`` is the game folder or a view of what the mod inherits.
    """

    country = Country(tag=tag)
    mod = as_view(mod_root)
    inherited = as_view(hoi4_install)
    assert mod is not None

    _read_definition(country, mod, inherited, _tag_mappings)
    _read_color(
        country,
        mod,
        inherited,
        _tag_mappings,
        _color_sources,
    )
    _read_history(country, mod, inherited)
    _read_character(country, mod, inherited)

    if _loc_cache is not None:
        _read_loc_from_cache(country, _loc_cache)
    else:
        _read_localisation(country, mod, inherited)

    return country


def _mod_definition_file(
    tag: str,
    mod_root: ContentView,
    hoi4_install: ContentView | None,
    tag_mappings: dict[ContentView, dict[str, str]] | None = None,
) -> Path | None:
    """The mod's definition file of ``tag``, or ``None``.

    Named by the mod's own tag file, else by the tag file of the game or a
    base mod: a mod file of the name they give replaces their definition
    without a tag file of the mod's own.
    """

    own = (tag_mappings or {}).get(mod_root)
    if own is None:
        own = tag_file_mapping(mod_root)
    if tag not in own and hoi4_install is not None:
        mapping = (tag_mappings or {}).get(hoi4_install)
        if mapping is None:
            mapping = tag_file_mapping(hoi4_install)
        relative = mapping.get(tag)
        if relative:
            found = mod_root.file(Path("common") / "countries" / Path(relative).name)
            if found is not None:
                return found
    return resolve_country_filename(mod_root, tag, own)


def _read_definition(
    country: Country,
    mod_root: ContentView,
    hoi4_install: ContentView | None,
    tag_mappings: dict[ContentView, dict[str, str]] | None = None,
) -> None:
    for base in [mod_root, hoi4_install]:
        if base is None:
            continue
        p = (
            _mod_definition_file(country.tag, mod_root, hoi4_install, tag_mappings)
            if base is mod_root
            else resolve_country_filename(base, country.tag, (tag_mappings or {}).get(base))
        )
        if p is not None:
            txt = read_text(p)
            country.definition_path = p.resolve()
            country.raw_definition = txt
            root = _root(txt)
            color = read_color(root)
            if color is not None:
                country.color = color
            for key in ("graphical_culture", "graphical_culture_2d"):
                value = root.scalar(key)
                if value:
                    setattr(country, key, value)
            return


def _read_color(
    country: Country,
    mod_root: ContentView,
    hoi4_install: ContentView | None,
    tag_mappings: dict[ContentView, dict[str, str]] | None = None,
    color_sources: dict[Path, str | None] | None = None,
) -> None:
    """Resolve country colour using the same vanilla-then-mod precedence as HOI4."""

    resolved: RGB | None = None
    resolved_ui: RGB | None = None
    # First the definition files, then the colour tables: an entry of
    # ``colors.txt`` wins over the colour of a definition file, also over
    # one of a higher layer.
    for base in (hoi4_install, mod_root):
        if base is None:
            continue
        definition = (
            _mod_definition_file(country.tag, mod_root, hoi4_install, tag_mappings)
            if base is mod_root
            else resolve_country_filename(base, country.tag, (tag_mappings or {}).get(base))
        )
        if definition is not None:
            color = read_color(_root(read_text(definition)))
            if color is not None:
                resolved = color
    for base in (hoi4_install, mod_root):
        if base is None:
            continue
        colors_path = base.file("common/countries/colors.txt")
        if colors_path is None:
            continue
        if color_sources is not None and colors_path in color_sources:
            text = color_sources[colors_path]
            if text is None:
                continue
        else:
            text = read_text(colors_path)
        entry = _first_block(_root(text), country.tag)
        if entry is None:
            continue
        color = read_color(entry)
        if color is not None:
            resolved = color
            resolved_ui = read_color(entry, "color_ui")
    if resolved is not None:
        country.color = resolved
    country.color_ui = resolved_ui


def _read_history(
    country: Country, mod_root: ContentView, hoi4_install: ContentView | None
) -> None:
    for base in [mod_root, hoi4_install]:
        if base is None:
            continue
        f = _find_history_file(base, country.tag)
        if not f:
            continue
        txt = read_text(f)
        country.history_path = f.resolve()
        country.raw_history = txt
        root = _root(txt)

        # Only direct statements are modeled: a value inside an ``if`` or a
        # date block is conditional, and edits patch the direct statement.
        capital = _int_or_none(root.scalar("capital"))
        if capital is not None:
            country.capital = capital

        research_slots = _int_or_none(root.scalar("set_research_slots"))
        if research_slots is not None:
            country.research_slots = research_slots

        for key, field_name in (
            ("set_stability", "stability"),
            ("set_war_support", "war_support"),
        ):
            value = _last_assignment(root, key)
            if value is not None and type(value.value) is Scalar:
                setattr(country, field_name, _coerce_history_scalar(value.value.raw))
        oob = _last_assignment(root, "set_oob") or _last_assignment(root, "oob")
        if oob is not None and type(oob.value) is Scalar:
            country.oob = oob.value.value
        technology = _last_assignment(root, "set_technology")
        if technology is not None and type(technology.value) is Block:
            country.technologies = {
                item.name: int(level)
                for item in technology.value.assignments()
                if item.op == "="
                and (level := item.scalar) is not None
                and level.lstrip("-").isdigit()
            }

        pops = _read_popularities(txt)
        if pops:
            country.popularities = pops

        ruling_party, elections_allowed = _read_politics(txt)
        politics = _first_block(_root(txt), "set_politics")
        if politics is not None:
            country.last_election = _last_scalar(politics, "last_election") or country.last_election
            country.election_frequency = _int_or_none(
                _last_scalar(politics, "election_frequency")
            )
        if ruling_party:
            country.ruling_party = ruling_party
            country.elections_allowed = (
                ruling_party == "democratic"
                if elections_allowed is None
                else elections_allowed
            )

        country.ideas = _read_assigned_ideas(txt)

        legacy_leader = _legacy_leader(txt)
        leader_name = legacy_leader[0] if legacy_leader else None
        recruited_ids = _read_recruited_character_ids(txt)
        country.recruited_characters = recruited_ids
        if legacy_leader:
            # An old ``create_country_leader`` block: the leader has a name
            # and an ideology, but no character id (one is only made when a
            # call changes the leader). A recruited character with a leader
            # role takes its place when the character file is read.
            country.leader = Leader(
                name=legacy_leader[0], character_id="", ideology=legacy_leader[1] or "liberalism",
            )
        elif recruited_ids:
            country.leader = Leader(
                name=leader_name or "",
                character_id=recruited_ids[0],
                portrait_slug=recruited_ids[0].removeprefix(f"{country.tag}_"),
            )

        return


def _read_localisation(
    country: Country, mod_root: ContentView, hoi4_install: ContentView | None
) -> None:
    for base in [mod_root, hoi4_install]:
        if base is None:
            continue
        folder = Path("localisation") / "english"
        candidates = [
            found
            for name in ("countries_l_english.yml", "countries_cosmetic_l_english.yml")
            if (found := base.file(folder / name)) is not None
        ]
        candidates.extend(
            f for f in base.rglob(folder, "*.yml").values() if f not in candidates
        )
        for f in candidates:
            raw = f.read_bytes()
            txt = decode_text(raw)
            if f"{country.tag}:" not in txt:
                continue
            for line in txt.splitlines():
                s = line.strip()
                if s.startswith(f"{country.tag}:"):
                    country.name = s.split(" ", 1)[-1].strip().strip('"')
                if s.startswith(f"{country.tag}_ADJ:"):
                    country.adjective = s.split(" ", 1)[-1].strip().strip('"')
            return


def _build_loc_cache(
    root: Path | ContentView,
    *,
    file_entries: Callable[[Path], dict[str, dict[str, str]]] | None = None,
) -> dict[str, dict[str, str]]:
    """Country name entries of every English localisation file, by tag.

    ``file_entries`` reads one file (as :func:`country_loc_entries` does on
    its text); pass one that keeps the answers on disk for inherited files.
    """

    cache: dict[str, dict[str, str]] = {}
    view = as_view(root)
    assert view is not None
    for f in view.rglob("localisation/english", "*.yml").values():
        found = (
            file_entries(f) if file_entries is not None
            else country_loc_entries(decode_text(f.read_bytes()))
        )
        for tag, entries in found.items():
            cache.setdefault(tag, {}).update(entries)
    return cache


def country_loc_entries(text: str) -> dict[str, dict[str, str]]:
    """``tag -> {key: text}`` of the lines of one localisation file that start with a tag."""

    cache: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "l_")):
            continue
        m = _COUNTRY_LOC_KEY_RE.match(s)
        if m:
            tag_key = m.group(1)
            tag = tag_key[:3] if len(tag_key) >= 3 and tag_key[:3].isupper() else None
            if tag:
                cache.setdefault(tag, {})[tag_key] = s.split(" ", 1)[-1].strip().strip('"')
    return cache


_COUNTRY_LOC_KEY_RE = re.compile(r"^\s*([A-Z0-9]{3}[A-Z0-9_]*):")


def _read_loc_from_cache(country: Country, cache: dict[str, dict[str, str]]) -> None:
    entries = cache.get(country.tag)
    if not entries:
        return
    for key, val in entries.items():
        if key == country.tag or key == f"{country.tag}:0":
            country.name = val
        elif key.startswith(f"{country.tag}_ADJ"):
            country.adjective = val


def _read_character(
    country: Country, mod_root: ContentView, hoi4_install: ContentView | None
) -> None:
    for base in [mod_root, hoi4_install]:
        if base is None:
            continue
        p = base.file(f"common/characters/{country.tag}_characters.txt")
        if p is None:
            p = base.file(f"common/characters/{country.tag}.txt")
        if p is None:
            continue
        txt = read_text(p)
        country.character_path = p.resolve()
        country.raw_character = txt

        definitions = _character_definitions(txt)
        recruited = _read_recruited_character_ids(country.raw_history)
        by_id = dict(definitions)
        leader_ids = [
            character_id
            for character_id, body in definitions
            if _leader_role(body) is not None
        ]
        selected_id = next((item for item in recruited if item in leader_ids), "")
        if not selected_id and country.leader and country.leader.character_id in by_id:
            selected_id = country.leader.character_id
        if not selected_id and leader_ids:
            selected_id = leader_ids[0]
        legacy = country.leader is not None and not country.leader.character_id
        if not selected_id and not legacy:
            # Without any leader role: the first recruited character stands
            # in, unless an old-style leader block names the leader.
            selected_id = next((item for item in recruited if item in by_id), "")
        if not selected_id:
            return

        character_body = by_id[selected_id]
        role = _leader_role(character_body)
        name = character_body.scalar("name") or ""
        ideology = (role.scalar("ideology") if role is not None else None) or "liberalism"
        portrait_prefix = f"GFX_portrait_{country.tag}_"
        portraits = sorted(
            (
                item
                for item, _ in character_body.walk()
                if item.name == "large"
                and (item.scalar or "").startswith(portrait_prefix)
            ),
            key=lambda item: item.start,
        )
        portrait_slug = (
            (portraits[0].scalar or "").removeprefix(portrait_prefix)
            if portraits
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
    for assignment in _root(history).assignments():
        if assignment.name not in {"add_ideas", "remove_ideas"} or assignment.op != "=":
            continue
        value = assignment.value
        if type(value) is Block:
            values = [item.value for item in value.values()]
        elif type(value) is Scalar:
            values = [value.value]
        else:
            continue
        if assignment.name == "add_ideas":
            assigned.extend(values)
        else:
            removed.update(values)
    return [idea for idea in dict.fromkeys(assigned) if idea not in removed]


def _last_assignment(block: Block, key: str) -> Assignment | None:
    found: Assignment | None = None
    for assignment in block.assignments(key):
        if assignment.op == "=":
            found = assignment
    return found


def _leader_role(character: Block) -> Block | None:
    """Return the character's ``country_leader`` block, directly or in an ``instance``."""

    role = _first_block(character, "country_leader")
    if role is not None:
        return role
    for instance in character.blocks("instance"):
        role = _first_block(instance, "country_leader")
        if role is not None:
            return role
    return None


def _direct_scalar(text: str, key: str) -> str:
    return _root(text).scalar(key) or ""


def _read_recruited_character_ids(history: str) -> list[str]:
    character_ids: list[str] = []
    for assignment in _root(history).assignments():
        if assignment.op != "=":
            continue
        value: str | None = None
        if assignment.name == "recruit_character":
            value = assignment.scalar
        elif assignment.name == "set_country_leader" and type(assignment.value) is Block:
            value = assignment.value.scalar("character")
        if value and value not in character_ids:
            character_ids.append(value)
    return character_ids


def _character_definitions(text: str) -> list[tuple[str, Block]]:
    root = _root(text)
    wrapper = _first_block(root, "characters") or root
    return [
        (item.name, item.value)
        for item in wrapper.assignments()
        if item.op == "=" and type(item.value) is Block
    ]


def _read_popularities(history: str) -> dict[str, int]:
    """Read every direct ideology group in ``set_popularities``.

    Party groups are data-driven in HOI4. Restricting this block to the four
    vanilla identifiers would silently discard custom ideologies when a
    country is edited and saved.
    """

    popularity = _first_block(_root(history), "set_popularities")
    if popularity is None:
        return {}
    values: dict[str, int] = {}
    for assignment in popularity.assignments():
        number = _int_or_none(assignment.scalar) if assignment.op == "=" else None
        if number is not None:
            values[assignment.name] = number
    return values


def _read_politics(history: str) -> tuple[str, bool | None]:
    politics = _first_block(_root(history), "set_politics")
    if politics is None:
        return "", None
    ruling_party = _last_scalar(politics, "ruling_party") or ""
    election_value = _last_scalar(politics, "elections_allowed")
    if election_value is None:
        return ruling_party, None
    return ruling_party, election_value.lower() in {"yes", "true", "1"}


def _popularity_body(popularities: dict[str, int]) -> str:
    ordered = [key for key in STANDARD_IDEOLOGY_GROUPS if key in popularities]
    ordered.extend(sorted(set(popularities) - set(ordered)))
    return "\n".join(
        require_entry(key, popularities[key], f"the popularity of '{key}'") for key in ordered
    )


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
    ui_colors: dict[str, tuple[int, int, int] | None] | None = None,
) -> str:
    """Patch country map/UI colours while retaining unrelated source text.

    ``color_ui`` follows the map colour unless ``ui_colors`` names one for the
    tag. Channels that already hold the wanted colour are left byte for byte,
    whatever their spelling (``rgb``, ``hsv`` or a plain block).
    """

    text = original
    for tag in sorted(deleted_tags or set()):
        span = _country_entry_span(text, tag)
        if span is not None:
            text = replace_assignment(text, span, None)
    for tag, color in sorted(colors.items()):
        if any(channel < 0 or channel > 255 for channel in color):
            raise ValueError(f"Country color for {tag} must use channels from 0 to 255")
        ui = (ui_colors or {}).get(tag) or color
        span = _country_entry_span(text, tag)
        if span is None or not span.is_block or span.tag is not None:
            r, g, b = color
            ur, ug, ub = ui
            entry = (
                f"{tag} = {{\n"
                f"\tcolor = rgb {{ {r} {g} {b} }}\n"
                f"\tcolor_ui = rgb {{ {ur} {ug} {ub} }}\n"
                "}"
            )
            if span is None:
                text = append_assignment(text, entry)
                if not text.endswith("\n"):
                    text += "\n"
            else:
                text = replace_assignment(text, span, entry)
            continue
        assert span.body_start is not None and span.body_end is not None
        body = text[span.body_start : span.body_end]
        patched = set_color(body, "color", color, default_tag="rgb")
        patched = set_color(patched, "color_ui", ui, default_tag="rgb")
        if patched != body and "\n" not in body and "\n" in patched:
            # ``ABC = { color = rgb { 1 2 3 } }`` grew a line: close it on its own.
            patched = patched.rstrip(" \t") + "\n"
        text = replace_assignment_body(text, span, patched)
    return text


def _country_entry_span(text: str, tag: str) -> AssignmentSpan | None:
    return next((item for item in top_level_assignments(text) if item.key == tag), None)


def country_table_colors(text: str, tag: str) -> tuple[RGB | None, RGB | None] | None:
    """``(color, color_ui)`` of ``tag`` in a ``colors.txt`` text, or ``None`` without an entry."""

    entry = _first_block(_root(text), tag)
    if entry is None:
        return None
    return read_color(entry), read_color(entry, "color_ui")


def country_color_tags(text: str) -> set[str]:
    """Return country tags defined by a ``common/countries/colors.txt`` source."""

    return {
        item.name
        for item in _root(text).assignments()
        if type(item.value) is Block and _COUNTRY_TAG_KEY_RE.fullmatch(item.name)
    }


def seed_country_colors_file(original: str, vanilla: str) -> str:
    """Append every vanilla country color entry missing from a mod file.

    A mod without its own table starts from the vanilla file verbatim, so the
    override differs from it only where a colour really changed.
    """

    if not original.strip():
        return vanilla
    text = original
    existing = country_color_tags(text)
    for span in top_level_assignments(vanilla):
        if (
            not span.is_block
            or span.tag is not None
            or span.key in existing
            or not _COUNTRY_TAG_KEY_RE.fullmatch(span.key)
        ):
            continue
        assignment = vanilla[span.start : span.end].strip()
        text = text.rstrip() + ("\n\n" if text.strip() else "") + assignment + "\n"
        existing.add(span.key)
    return text


def _find_history_file(base: ContentView, tag: str) -> Path | None:
    for pattern in (f"{tag} - *.txt", f"{tag}*.txt"):
        for f in base.glob("history/countries", pattern).values():
            return f
    return None


def _legacy_leader(txt: str) -> tuple[str, str] | None:
    """``(name, ideology)`` of the first legacy ``create_country_leader`` block with a name."""

    leaders = sorted(
        (
            item
            for item, _ in _root(txt).walk()
            if item.name == "create_country_leader" and type(item.value) is Block
        ),
        key=lambda item: item.start,
    )
    for leader in leaders:
        assert type(leader.value) is Block
        name = leader.value.scalar("name")
        if name:
            return name, leader.value.scalar("ideology") or ""
    return None


def remove_legacy_leader(history: str, name: str) -> str:
    """Remove the first ``create_country_leader`` block of the leader ``name``.

    For the moment a character takes the place of an old-style leader: the
    game would otherwise create both. The block may sit inside ``if = { }``
    or a dated block. Blocks of other leaders stay.
    """

    for span in top_level_assignments(history):
        if not span.is_block or span.body_start is None or span.body_end is None:
            continue
        body = history[span.body_start : span.body_end]
        if span.key == "create_country_leader":
            if _root(history[span.start : span.end]).block(span.key).scalar("name") == name:  # type: ignore[union-attr]
                return replace_assignment(history, span, None)
            continue
        inner = remove_legacy_leader(body, name)
        if inner != body:
            return replace_assignment_body(history, span, inner)
    return history


def write_country_tag(mod_root: Path, tag: str) -> Path:
    return add_country_tag(mod_root, tag)


def remove_country_tag(mod_root: Path, tag: str) -> Path | None:
    return _remove_country_tag(mod_root, tag)


def write_country_definition(mod_root: Path, country: Country) -> None:
    p = country.definition_path or mod_root / f"common/countries/{country.tag}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_country_files(mod_root, country)[p]
    write_text(p, content)


def write_country_history(mod_root: Path, country: Country) -> None:
    tag = country.tag
    p = country_history_target(mod_root, country)
    p.parent.mkdir(parents=True, exist_ok=True)

    for old in p.parent.glob(f"{tag} - *.txt"):
        if old != p:
            old.unlink()

    serialized = serialize_country_files(mod_root, country)
    write_text(p, serialized[p])


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
    write_text(loc, "\n".join(lines), bom=True)


def write_character_file(mod_root: Path, country: Country) -> None:
    if not country.leader or not country.leader.character_id:
        return
    serialized = serialize_country_files(mod_root, country)
    tag = country.tag
    p = country.character_path or mod_root / f"common/characters/{tag}_characters.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, serialized[p])


def write_all_country_files(mod_root: Path, country: Country) -> list[Path]:
    written = [write_country_tag(mod_root, country.tag)]
    write_country_definition(mod_root, country)
    written.append(country.definition_path or mod_root / f"common/countries/{country.tag}.txt")
    write_country_history(mod_root, country)
    written.append(country_history_target(mod_root, country))
    write_character_file(mod_root, country)
    if country.leader and country.leader.character_id:
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
    if country.leader and country.leader.character_id:
        paths.append(country.character_path or mod_root / f"common/characters/{tag}_characters.txt")
    return paths


def country_history_target(
    mod_root: Path, country: Country, *, keep_history_path: bool = False
) -> Path:
    """The history file a save writes for ``country``.

    A name change renames the file to ``TAG - Name.txt`` unless
    ``keep_history_path`` is true (see :func:`serialize_country_files`).
    """

    tag = require_country_tag(country.tag)
    renamed = {"*", "name"} & country.touched_fields and not keep_history_path
    if country.history_path is not None and not renamed:
        return country.history_path
    safe_name = safe_file_stem(country.name or tag, fallback=tag)
    return mod_root / f"history/countries/{tag} - {safe_name}.txt"


def serialize_country_files(
    mod_root: Path,
    country: Country,
    *,
    keep_history_path: bool = False,
) -> dict[Path, str]:
    """Render the country's definition, history and leader files.

    A name change renames the history file to ``TAG - Name.txt`` unless
    ``keep_history_path`` is true. Pass it when the history file overrides an
    inherited one: the game would otherwise load both files.
    """

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
            f"graphical_culture = {quote(country.graphical_culture)}\n"
            f"graphical_culture_2d = {quote(country.graphical_culture_2d)}\n"
            f"color = {{ {r} {g} {b} }}\n"
        )
    else:
        if "*" in country.touched_fields or "graphical_culture" in country.touched_fields:
            definition = set_scalar(
                definition, "graphical_culture", quote(country.graphical_culture)
            )
        if "*" in country.touched_fields or "graphical_culture_2d" in country.touched_fields:
            definition = set_scalar(
                definition, "graphical_culture_2d", quote(country.graphical_culture_2d)
            )
        if "*" in country.touched_fields or "color" in country.touched_fields:
            definition = set_color(definition, "color", (r, g, b))
    files[def_path] = definition

    hist_path = country_history_target(mod_root, country, keep_history_path=keep_history_path)
    leader = country.leader
    recruited_characters = list(
        dict.fromkeys(
            [
                *(country.recruited_characters or []),
                *([leader.character_id] if leader is not None and leader.character_id else []),
            ]
        )
    )
    recruitment_lines = "".join(
        f"recruit_character = {quote(character_id)}\n"
        for character_id in recruited_characters
    )
    elections = "yes" if country.elections_allowed else "no"
    election_frequency_line = (
        f" election_frequency = {int(country.election_frequency)}\n"
        if country.election_frequency is not None
        else ""
    )
    pops = country.popularities
    ideas_block = ""
    if country.ideas:
        ideas_lines = "\n".join(
            f" {quote(idea)}" for idea in country.ideas
        )
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
    oob = f"set_oob = {pdx_string(country.oob)}\n" if country.oob else ""
    technologies = ""
    if country.technologies:
        technology_lines = "\n".join(
            " " + require_entry(technology, level, f"the technology '{technology}'")
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
        f"{recruitment_lines}"
        f"\n"
        f"set_popularities = {{\n"
        f"{popularity_lines}\n"
        f"}}\n"
        f"\n"
        f"set_politics = {{\n"
        f" ruling_party = {quote(country.ruling_party)}\n"
        f" last_election = {pdx_string(country.last_election)}\n"
        f"{election_frequency_line}"
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
            history = set_scalar(history, "oob", None)
            history = set_scalar(
                history,
                "set_oob",
                pdx_string(country.oob) if country.oob else None,
            )
        if "*" in touched or "recruited_characters" in touched:
            history = _patch_repeated_scalars(
                history,
                "recruit_character",
                recruited_characters,
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
            history = _patch_politics(history, country.ruling_party, elections)
        if {"last_election", "election_frequency"} & touched:
            history = _patch_election(history, country, touched)
        if "*" in touched or "ideas" in touched:
            history = set_block(
                history, "add_ideas", "\n".join(quote(idea) for idea in country.ideas) if country.ideas else None
            )
            history = set_block(history, "remove_ideas", None)
    files[hist_path] = history

    if country.leader and country.leader.character_id:
        char_path = country.character_path or mod_root / f"common/characters/{tag}_characters.txt"
        ld = country.leader
        portrait_key = ld.portrait_slug or ld.character_id.removeprefix(f"{tag}_")
        generated_character = (
            f"characters = {{\n"
            f" {require_key(ld.character_id, 'a character id')} = {{\n"
            f"  name = {pdx_string(ld.name)}\n"
            f"\n"
            f"  portraits = {{\n"
            f"   civilian = {{\n"
            f"    large = {quote(f'GFX_portrait_{tag}_{portrait_key}')}\n"
            f"   }}\n"
            f"  }}\n"
            f"\n"
            f"  country_leader = {{\n"
            f"   ideology = {quote(ld.ideology)}\n"
            f"   desc = {quote(ld.character_id + '_desc')}\n"
            f'   expire = "1965.1.1"\n'
            f"   traits = {{ }}\n"
            f"  }}\n"
            f" }}\n"
            f"}}\n"
        )
        # Existing character files may define advisors/generals alongside the leader.
        # Preserve them byte-for-byte unless a leader field was explicitly changed.
        character = country.raw_character
        if (
            character
            and any(
                field.startswith("leader_") or field == "*" for field in country.touched_fields
            )
            # A leader that just became a character is not in the file yet:
            # the character itself is written with the file's other characters.
            and _has_character(character, ld.character_id)
        ):
            character = _patch_character(character, ld, country.touched_fields, tag)
        files[char_path] = character or generated_character

    return files


def _start_politics(history: str) -> AssignmentSpan | None:
    """The ``set_politics`` block of the start date (not one in a dated block)."""

    return next(
        (
            item
            for item in top_level_assignments(history)
            if item.key == "set_politics" and item.is_block and item.tag is None
        ),
        None,
    )


def has_start_politics(history: str) -> bool:
    """Whether the history sets politics at the start date."""

    return _start_politics(history) is not None


def _patch_election(history: str, country: Country, touched: set[str]) -> str:
    """Set ``last_election`` and ``election_frequency`` of ``set_politics`` when they changed."""

    span = _start_politics(history)
    if span is None or span.body_start is None or span.body_end is None:
        return history
    body = history[span.body_start : span.body_end]
    patched = body
    if "last_election" in touched:
        patched = set_scalar(patched, "last_election", pdx_string(country.last_election))
    if "election_frequency" in touched:
        patched = set_scalar(
            patched,
            "election_frequency",
            str(int(country.election_frequency)) if country.election_frequency is not None else None,
        )
    return history if patched == body else replace_assignment_body(history, span, patched)


def _patch_politics(history: str, ruling_party: str, elections: str) -> str:
    """Set the party and elections of ``set_politics`` and keep its other fields.

    ``last_election`` and any unknown key in an existing block stay as written.
    """

    span = next(
        (
            item
            for item in top_level_assignments(history)
            if item.key == "set_politics" and item.is_block and item.tag is None
        ),
        None,
    )
    if span is None or span.body_start is None or span.body_end is None:
        return set_block(
            history,
            "set_politics",
            "\n".join(
                [
                    f"ruling_party = {quote(ruling_party)}",
                    'last_election = "1936.1.1"',
                    f"elections_allowed = {elections}",
                ]
            ),
        )
    body = history[span.body_start : span.body_end]
    patched = set_scalar(body, "ruling_party", quote(ruling_party))
    patched = set_scalar(patched, "elections_allowed", elections)
    if patched == body:
        return history
    if "\n" not in body and "\n" in patched:
        patched = patched.rstrip(" \t") + "\n"
    return replace_assignment_body(history, span, patched)


def _patch_repeated_scalars(text: str, key: str, values: list[str]) -> str:
    """Patch a repeated scalar list while retaining surviving source entries."""

    desired = list(dict.fromkeys(value for value in values if value))
    spans = [
        span
        for span in top_level_assignments(text)
        if span.key == key and not span.is_block
    ]
    seen: set[str] = set()
    keep: set[int] = set()
    for index, span in enumerate(spans):
        value = _unquote_history_scalar(text[span.value_start : span.value_end])
        if value in desired and value not in seen:
            keep.add(index)
            seen.add(value)
    for index, span in reversed(list(enumerate(spans))):
        if index not in keep:
            text = replace_assignment(text, span, None)
    for value in desired:
        if value not in seen:
            text = append_assignment(text, f"{key} = {quote(value)}")
    return text


def _set_scalar_mapping_block(
    text: str,
    key: str,
    values: dict[str, int],
) -> str:
    spans = [span for span in top_level_assignments(text) if span.key == key]
    if not values:
        return set_block(text, key, None)
    rendered = "\n".join(
        require_entry(name, level, f"the entry '{name}' of '{key}'")
        for name, level in values.items()
    )
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


def _has_character(text: str, character_id: str) -> bool:
    """Whether a characters file defines ``character_id``."""

    block = _root(text).block("characters")
    return block is not None and block.block(character_id) is not None


def _patch_character(
    text: str,
    leader: Leader,
    touched_fields: set[str],
    tag: str,
) -> str:
    root = next(
        (
            span
            for span in top_level_assignments(text)
            if span.key == "characters" and span.is_block and span.tag is None
        ),
        None,
    )
    body = (
        text[root.body_start : root.body_end]
        if root is not None and root.body_start is not None and root.body_end is not None
        else text
    )
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
        leader_body = set_scalar(
            leader_body, "name", _string_like(leader_body, "name", leader.name)
        )
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
        role_body = set_scalar(role_body, "ideology", quote(leader.ideology))
        leader_body = replace_assignment_body(leader_body, role, role_body)
    if "*" in touched_fields or "leader_portrait_slug" in touched_fields:
        leader_body = _patch_character_portrait(leader_body, tag, leader.portrait_slug)
    body = replace_assignment_body(body, leader_span, leader_body)
    if root is None:
        return body
    return replace_assignment_body(text, root, body)


def _patch_character_portrait(body: str, tag: str, portrait_slug: str) -> str:
    portrait_key = quote(f"GFX_portrait_{tag}_{portrait_slug}")
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
