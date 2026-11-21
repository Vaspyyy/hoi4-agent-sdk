"""Country lookup, tags, politics helpers and country overrides."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from ..countries import (
    country_file_paths,
    read_country,
    serialize_country_files,
)
from ..oob import (
    OOBReference,
    find_oob_references,
)
from ..patching import (
    move_assignment_before,
    set_block,
    top_level_assignments,
)
from ..paths import (
    require_country_tag,
    resolve_mod_output_path,
)
from ..politics import LEADER_IDEOLOGIES_BY_PARTY, RULING_PARTIES
from ..script import (
    pdx_string,
    pdx_value,
)
from ..syntax import repair, require_entry
from ..textio import read_text
from ..types import (
    Country,
)
from ._core import CoreLayer


def _normalize_name_token(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


class CountriesLayer(CoreLayer):
    """Country lookup, tags, politics helpers and country overrides."""

    # ── Countries ────────────────────────────────────────────────

    def list_countries(self) -> list[str]:
        return sorted(self._countries.keys())

    def is_country_tag_available(self, tag: str) -> bool:
        tag = tag.upper()
        return tag not in self._countries and tag not in self._vanilla_tags

    def country_tag_conflicts(self, tag: str) -> list[str]:
        tag = tag.upper()
        conflicts: list[str] = []
        if tag in self._countries:
            country = self._countries[tag]
            label = country.name or tag
            conflicts.append(f"mod: {tag} ({label})")
        if tag in self._vanilla_tags:
            country = read_country(
                self.mod_root,
                tag,
                self._base_content_root,
                _tag_mappings=self._country_tag_mappings,
                _color_sources=self._country_color_sources,
            )
            label = country.name or tag
            conflicts.append(f"vanilla: {tag} ({label})")
        return conflicts

    def suggest_tag(self, name: str) -> str:
        suggestions = self.suggest_tags(name, count=1)
        if not suggestions:
            raise ValueError("No available 3-character country tags remain")
        return suggestions[0]

    def suggest_tags(self, name: str, count: int = 5) -> list[str]:
        taken = set(self._countries) | self._vanilla_tags
        token = _normalize_name_token(name)
        candidates: list[str] = []

        def add(candidate: str) -> None:
            candidate = _normalize_name_token(candidate)[:3]
            if len(candidate) == 3 and candidate not in candidates and candidate not in taken:
                candidates.append(candidate)

        if len(token) >= 3:
            add(token[:3])
            consonants = "".join(ch for ch in token if ch not in "AEIOU")
            if len(consonants) >= 3:
                add(consonants[:3])
            add(token[0] + token[1] + token[-1])
            add(token[0] + token[-2:])
            for i in range(1, len(token) - 1):
                add(token[0] + token[i] + token[-1])
            for i in range(len(token) - 2):
                add(token[i : i + 3])

        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        prefix = token[:1] or "X"
        for second in alphabet:
            for third in alphabet:
                add(prefix + second + third)
                if len(candidates) >= count:
                    return candidates[:count]
        for first in alphabet:
            for second in alphabet:
                for third in alphabet:
                    add(first + second + third)
                    if len(candidates) >= count:
                        return candidates[:count]
        return candidates[:count]

    def get_country(self, tag: str) -> Country:
        tag = require_country_tag(tag)
        if tag in self._countries:
            return self._countries[tag]
        country = read_country(
            self.mod_root,
            tag,
            self._base_content_root,
            _tag_mappings=self._country_tag_mappings,
            _color_sources=self._country_color_sources,
        )
        self._countries[tag] = country
        for path in country_file_paths(self.mod_root, country):
            if path.exists():
                self._original_files.setdefault(
                    path, read_text(path)
                )
                if path.resolve(strict=False).is_relative_to(self.mod_root):
                    self._source_baseline.setdefault(
                        path.resolve(strict=False), path.read_bytes()
                    )
        return country

    def set_country_name_pool(
        self,
        tag: str,
        *,
        male_names: Sequence[str],
        surnames: Sequence[str],
        female_names: Sequence[str] = (),
        callsigns: Sequence[str] = (),
    ) -> None:
        """Define random character and ace names for one country tag."""

        tag = require_country_tag(tag)

        def values(items: Sequence[str], *, required: bool, label: str) -> str:
            normalized = tuple(
                dict.fromkeys(item.strip() for item in items if item.strip())
            )
            if required and not normalized:
                raise ValueError(
                    f"Country name pool requires at least one {label}"
                )
            return " ".join(pdx_string(item) for item in normalized)

        male = values(male_names, required=True, label="male name")
        family = values(surnames, required=True, label="surname")
        female = values(female_names, required=False, label="female name")
        call = values(callsigns, required=False, label="callsign")
        sections = [f"male = {{\n\tnames = {{ {male} }}\n}}"]
        if female:
            sections.append(f"female = {{\n\tnames = {{ {female} }}\n}}")
        sections.append(f"surnames = {{ {family} }}")
        if call:
            sections.append(f"callsigns = {{ {call} }}")
        self._country_name_pool_updates[tag] = "\n".join(sections)
        self._dirty.add("country_names")
        self._scan_cache.pop("country_name_pools", None)

    @staticmethod
    def _country_oob_references(country: Country) -> tuple[OOBReference, ...]:
        references = list(find_oob_references(country.raw_history))
        if country.oob and not any(
            reference.kind == "land" and reference.name == country.oob
            for reference in references
        ):
            references.append(OOBReference(country.oob, "land"))
        return tuple(references)

    def _materialize_country_history(self, country: Country) -> None:
        if country.raw_history:
            return
        files = serialize_country_files(self.mod_root, country)
        history_path = next(
            path for path in files if path.parent.name == "countries"
            and path.parent.parent.name == "history"
        )
        country.history_path = history_path
        country.raw_history = files[history_path]

    @staticmethod
    def _synchronize_pending_country_technologies(country: Country) -> None:
        """Place pending model technologies before every equipment variant."""

        if not ({"*", "technologies"} & country.touched_fields):
            return
        body = "\n".join(
            require_entry(technology, pdx_value(level), f"the technology '{technology}'")
            for technology, level in country.technologies.items()
        )
        history = set_block(
            country.raw_history,
            "set_technology",
            body or None,
        )
        if not body:
            country.raw_history = history
            return

        spans = top_level_assignments(history)
        technology_span = next(
            (span for span in spans if span.key == "set_technology"),
            None,
        )
        if technology_span is None:
            country.raw_history = history
            return

        def contains_variant(fragment: str) -> bool:
            for span in top_level_assignments(fragment):
                if span.key == "create_equipment_variant":
                    return True
                if (
                    span.is_block
                    and span.body_start is not None
                    and span.body_end is not None
                    and contains_variant(fragment[span.body_start : span.body_end])
                ):
                    return True
            return False

        variant_container = next(
            (
                span
                for span in spans
                if span is not technology_span
                and span.is_block
                and span.body_start is not None
                and span.body_end is not None
                and contains_variant(history[span.start : span.end])
            ),
            None,
        )
        if (
            variant_container is None
            or technology_span.start < variant_container.start
        ):
            country.raw_history = history
            return

        country.raw_history = move_assignment_before(
            history,
            technology_span,
            variant_container,
        )

    def _materialize_country_override(self, country: Country) -> None:
        """Retarget vanilla-backed country files into equivalent mod paths."""

        path_fields = (
            ("definition_path", "raw_definition", Path("common/countries") / f"{country.tag}.txt"),
            (
                "history_path",
                "raw_history",
                Path("history/countries")
                / f"{country.tag} - {country.name or country.tag}.txt",
            ),
            (
                "character_path",
                "raw_character",
                Path("common/characters") / f"{country.tag}_characters.txt",
            ),
        )
        for field_name, raw_field, fallback in path_fields:
            source = getattr(country, field_name)
            if source is None or source.resolve(strict=False).is_relative_to(self.mod_root):
                continue
            # The game tolerates a missing closing brace in its own files
            # (Norway's history); the mod's copy is edited and written well formed.
            setattr(country, raw_field, repair(getattr(country, raw_field)))
            relative = fallback
            if self._base_content_root is not None:
                try:
                    relative = source.resolve(strict=False).relative_to(self._base_content_root)
                except ValueError:
                    pass
            target = resolve_mod_output_path(self.mod_root, relative)
            setattr(country, field_name, target)
            self._original_files.setdefault(target, self._read_current_text(target))

    @staticmethod
    def default_leader_ideology(ruling_party: str) -> str:
        return LEADER_IDEOLOGIES_BY_PARTY.get(
            ruling_party, LEADER_IDEOLOGIES_BY_PARTY["democratic"]
        )[0]

    @staticmethod
    def leader_ideologies_for_party(ruling_party: str) -> tuple[str, ...]:
        return LEADER_IDEOLOGIES_BY_PARTY.get(ruling_party, ())

    @staticmethod
    def ruling_parties() -> tuple[str, ...]:
        return RULING_PARTIES

    def available_ruling_parties(self, *, include_vanilla: bool = True) -> tuple[str, ...]:
        """Return ideology groups available to this mod, including custom groups."""

        parties = set(RULING_PARTIES) | set(self._ideologies)
        if include_vanilla:
            parties.update(self._vanilla_ideologies)
        return tuple(sorted(parties))

    def available_leader_ideologies(
        self, ruling_party: str, *, include_vanilla: bool = True
    ) -> tuple[str, ...]:
        """Return subtype IDs for a vanilla or custom ideology group."""

        ideology = self._ideologies.get(ruling_party)
        if ideology is None and include_vanilla:
            ideology = self._vanilla_ideologies.get(ruling_party)
        if ideology is not None:
            return tuple(subtype.name for subtype in ideology.types)
        return self.leader_ideologies_for_party(ruling_party)

    def _default_leader_ideology_for_party(self, ruling_party: str) -> str:
        available = self.available_leader_ideologies(ruling_party)
        if available:
            return available[0]
        return self.default_leader_ideology(ruling_party)

    def _is_known_country_tag(self, tag: str) -> bool:
        return (
            tag in self._countries
            or tag in self._vanilla_tags
            or any(
                tag in mapping
                for mapping in self._country_tag_mappings.values()
            )
        )
