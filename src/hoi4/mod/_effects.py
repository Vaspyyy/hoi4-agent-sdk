"""Builders that return ready-made effect script."""

from __future__ import annotations

import warnings
from typing import Sequence

from ..effects_catalog import TECHNOLOGY_CATEGORIES
from ..paths import (
    require_country_tag,
    require_script_id,
)
from ..script import (
    braced,
    effect_block,
    normalize_block_body,
    pdx_string,
    pdx_value,
    scope_block,
)
from ..syntax import quote, require_well_formed
from ._core import CoreLayer
from ._shared import (
    _join_script_parts,
)


class EffectsLayer(CoreLayer):
    """Builders that return ready-made effect script."""

    @staticmethod
    def effect_add_building(state_id: int, building_type: str, level: int = 1) -> str:
        return (
            f"{state_id} = {{ "
            f"add_extra_state_shared_building_slots = {level} "
            "add_building_construction = { "
            f"type = {quote(building_type)} level = {level} instant_build = yes "
            "} "
            "}"
        )

    @staticmethod
    def effect_block(name: str, fields: dict[str, object] | None = None, **kwargs: object) -> str:
        return effect_block(name, fields, **kwargs)

    @staticmethod
    def scope_block(scope: str | int, *effects: str) -> str:
        return scope_block(scope, *effects)

    @staticmethod
    def effect_add_state_building(
        state_id: int,
        building_type: str,
        level: int = 1,
        *,
        add_slots: int = 0,
        province: int | None = None,
    ) -> str:
        fields: dict[str, object] = {
            "type": building_type,
            "level": level,
            "instant_build": True,
        }
        if province is not None:
            fields["province"] = province
        effects: list[str] = []
        if add_slots:
            effects.append(f"add_extra_state_shared_building_slots = {add_slots}")
        effects.append(effect_block("add_building_construction", fields))
        return scope_block(state_id, *effects)

    @classmethod
    def effect_add_civilian_factory(cls, state_id: int, level: int = 1) -> str:
        return cls.effect_add_building(state_id, "industrial_complex", level)

    @classmethod
    def effect_add_military_factory(cls, state_id: int, level: int = 1) -> str:
        return cls.effect_add_building(state_id, "arms_factory", level)

    @staticmethod
    def effect_add_infrastructure(state_id: int, level: int = 1) -> str:
        return (
            f"{state_id} = {{ "
            "add_building_construction = { "
            f"type = infrastructure level = {level} instant_build = yes "
            "} "
            "}"
        )

    @classmethod
    def effect_add_bunker(cls, state_id: int, level: int = 1, province: int | None = None) -> str:
        return cls.effect_add_state_building(state_id, "bunker", level, province=province)

    @classmethod
    def effect_add_coastal_bunker(
        cls, state_id: int, level: int = 1, province: int | None = None
    ) -> str:
        return cls.effect_add_state_building(state_id, "coastal_bunker", level, province=province)

    @staticmethod
    def effect_add_tech_bonus(
        name: str,
        category: str = "industry",
        uses: int = 1,
        bonus: float = 0.5,
    ) -> str:
        if category not in TECHNOLOGY_CATEGORIES:
            examples = ", ".join(
                (
                    "industry",
                    "infantry_weapons",
                    "artillery",
                    "armor",
                    "electronics",
                    "land_doctrine",
                )
            )
            raise ValueError(
                f"Unknown technology category '{category}'. Common valid categories: {examples}"
            )
        return (
            f"add_tech_bonus = {{ name = {quote(name)} bonus = {pdx_value(bonus)} "
            f"uses = {pdx_value(uses)} category = {category} }}"
        )

    @classmethod
    def effect_add_industry_bonus(cls, name: str, uses: int = 1, bonus: float = 0.5) -> str:
        return cls.effect_add_tech_bonus(name, category="industry", uses=uses, bonus=bonus)

    @staticmethod
    def effect_add_state_core(state_id: int, tag: str) -> str:
        return f"{state_id} = {{ add_core_of = {require_country_tag(tag)} }}"

    @staticmethod
    def effect_remove_state_core(state_id: int, tag: str) -> str:
        return f"{state_id} = {{ remove_core_of = {require_country_tag(tag)} }}"

    @staticmethod
    def effect_transfer_state(state_id: int, target: str) -> str:
        return f"{require_country_tag(target)} = {{ transfer_state = {state_id} }}"

    @classmethod
    def effect_transfer_state_with_core(cls, state_id: int, target: str) -> str:
        return "\n".join(
            [
                cls.effect_transfer_state(state_id, target),
                cls.effect_add_state_core(state_id, target),
            ]
        )

    @staticmethod
    def effect_add_political_power(amount: int) -> str:
        return f"add_political_power = {amount}"

    @staticmethod
    def effect_add_war_support(amount: float) -> str:
        return f"add_war_support = {amount}"

    @staticmethod
    def effect_add_stability(amount: float) -> str:
        return f"add_stability = {amount}"

    @staticmethod
    def effect_add_manpower(amount: int) -> str:
        return f"add_manpower = {amount}"

    @staticmethod
    def effect_add_army_experience(amount: int | float) -> str:
        return f"army_experience = {amount}"

    @staticmethod
    def effect_add_navy_experience(amount: int | float) -> str:
        return f"navy_experience = {amount}"

    @staticmethod
    def effect_add_air_experience(amount: int | float) -> str:
        return f"air_experience = {amount}"

    @staticmethod
    def effect_add_equipment(
        equipment_type: str,
        amount: int,
        producer: str | None = None,
        variant_name: str | None = None,
    ) -> str:
        fields: dict[str, object] = {"type": equipment_type, "amount": amount}
        if producer:
            fields["producer"] = require_country_tag(producer)
        if variant_name:
            fields["variant_name"] = variant_name
        return effect_block("add_equipment_to_stockpile", fields)

    @staticmethod
    def effect_set_technology(technology: str, level: int = 1, popup: bool | None = None) -> str:
        technology = require_script_id(technology, label="technology ID")
        fields: dict[str, object] = {technology: level}
        if popup is not None:
            fields["popup"] = popup
        return effect_block("set_technology", fields)

    @classmethod
    def effect_set_technologies(cls, technologies: dict[str, int]) -> str:
        return effect_block(
            "set_technology",
            {
                require_script_id(technology, label="technology ID"): level
                for technology, level in technologies.items()
            },
        )

    @staticmethod
    def effect_add_timed_idea(idea_id: str, days: int) -> str:
        idea_id = require_script_id(idea_id, label="idea ID")
        return f"add_timed_idea = {{ idea = {idea_id} days = {days} }}"

    @staticmethod
    def effect_add_dynamic_modifier(
        modifier_id: str,
        *,
        days: int | str | None = None,
        scope: str | int | None = None,
    ) -> str:
        fields: dict[str, object] = {
            "modifier": require_script_id(
                modifier_id,
                label="dynamic modifier ID",
            )
        }
        if days is not None:
            fields["days"] = days
        if scope is not None:
            fields["scope"] = scope
        return effect_block("add_dynamic_modifier", fields)

    @staticmethod
    def effect_remove_dynamic_modifier(
        modifier_id: str,
        *,
        scope: str | int | None = None,
    ) -> str:
        fields: dict[str, object] = {
            "modifier": require_script_id(
                modifier_id,
                label="dynamic modifier ID",
            )
        }
        if scope is not None:
            fields["scope"] = scope
        return effect_block("remove_dynamic_modifier", fields)

    @staticmethod
    def effect_force_update_dynamic_modifier() -> str:
        return "force_update_dynamic_modifier = yes"

    @staticmethod
    def effect_swap_idea(old: str, new: str, target: str | None = None) -> str:
        old = require_script_id(old, label="idea ID")
        new = require_script_id(new, label="idea ID")
        effect = f"swap_ideas = {{ remove_idea = {old} add_idea = {new} }}"
        return scope_block(require_country_tag(target), effect) if target else effect

    @classmethod
    def effect_upgrade_idea_chain(cls, ideas: list[str], target: str | None = None) -> str:
        if len(ideas) < 2:
            raise ValueError("effect_upgrade_idea_chain() requires at least two idea IDs")
        ideas = [require_script_id(idea, label="idea ID") for idea in ideas]
        effects: list[str] = []
        for old, new in reversed(list(zip(ideas, ideas[1:]))):
            effects.append(
                f"if = {{ limit = {{ has_idea = {old} }} {cls.effect_swap_idea(old, new)} }}"
            )
        missing_checks = " ".join(f"NOT = {{ has_idea = {idea} }}" for idea in ideas)
        effects.append(f"if = {{ limit = {{ {missing_checks} }} add_ideas = {ideas[0]} }}")
        body = "\n".join(effects)
        return scope_block(require_country_tag(target), body) if target else body

    @staticmethod
    def effect_create_wargoal(target: str, war_goal_type: str = "annex_everything") -> str:
        target = require_country_tag(target)
        war_goal_type = require_script_id(war_goal_type, label="war goal type")
        return f"create_wargoal = {{ type = {war_goal_type} target = {target} }}"

    @staticmethod
    def effect_declare_war(target: str, war_goal_type: str = "annex_everything") -> str:
        target = require_country_tag(target)
        war_goal_type = require_script_id(war_goal_type, label="war goal type")
        return f"declare_war_on = {{ type = {war_goal_type} target = {target} }}"

    @classmethod
    def effect_declare_war_from(
        cls,
        actor: str,
        target: str,
        war_goal_type: str = "annex_everything",
    ) -> str:
        return cls.scope_block(
            require_country_tag(actor), cls.effect_declare_war(target, war_goal_type)
        )

    @staticmethod
    def effect_start_civil_war(
        ideology: str,
        size: float = 0.5,
        capital: int | None = None,
        *,
        effects: Sequence[str] | None = None,
    ) -> str:
        ideology = require_script_id(ideology, label="ideology ID")
        parts = [f"ideology = {ideology}", f"size = {size}"]
        if capital is not None:
            if not isinstance(capital, int) or isinstance(capital, bool) or capital <= 0:
                raise ValueError("Civil-war capital must be a positive state ID")
            parts.append(f"capital = {capital}")
        nested = [effect.strip() for effect in effects or () if effect and effect.strip()]
        for effect in nested:
            # A stray brace would end the civil war block early.
            require_well_formed(effect, "an effect of start_civil_war")
        if not nested:
            return f"start_civil_war = {{ {' '.join(parts)} }}"
        body = "\n".join(
            [
                *(f"\t{part}" for part in parts),
                *(
                    "\n".join(f"\t{line}" for line in effect.splitlines())
                    for effect in nested
                ),
            ]
        )
        return f"start_civil_war = {{\n{body}\n}}"

    @staticmethod
    def effect_load_focus_tree(
        tree_id: str,
        *,
        keep_completed: bool = False,
        copy_completed_from: str | None = None,
        mark_layout_dirty: bool = True,
    ) -> str:
        tree_id = require_script_id(tree_id, label="focus tree ID")
        fields: dict[str, object] = {"tree": tree_id, "keep_completed": keep_completed}
        if copy_completed_from:
            fields["copy_completed_from"] = require_country_tag(copy_completed_from)
        effects = [effect_block("load_focus_tree", fields)]
        if mark_layout_dirty:
            effects.append("mark_focus_tree_layout_dirty = yes")
        return "\n".join(effects)

    @classmethod
    def effect_spawn_civil_war_with_focus_tree(
        cls,
        ideology: str,
        tree_id: str,
        *,
        size: float = 0.5,
        capital: int | None = None,
        keep_completed: bool = False,
        copy_completed_from: str | None = None,
        mark_layout_dirty: bool = True,
    ) -> str:
        load_tree = cls.effect_load_focus_tree(
            tree_id,
            keep_completed=keep_completed,
            copy_completed_from=copy_completed_from,
            mark_layout_dirty=mark_layout_dirty,
        )
        return cls.effect_start_civil_war(
            ideology,
            size=size,
            capital=capital,
            effects=[load_tree],
        )

    @staticmethod
    def effect_set_politics(
        ruling_party: str,
        *,
        elections_allowed: bool | None = None,
        elections_frequency: int | None = None,
    ) -> str:
        ruling_party = require_script_id(ruling_party, label="ruling party")
        if elections_frequency is not None:
            raise ValueError(
                "HOI4's set_politics effect does not support elections_frequency; "
                "remove this argument"
            )
        fields: dict[str, object] = {"ruling_party": ruling_party}
        if elections_allowed is not None:
            fields["elections_allowed"] = elections_allowed
        return effect_block("set_politics", fields)

    @staticmethod
    def effect_create_faction(name: str) -> str:
        return f"create_faction = {pdx_string(name)}"

    @staticmethod
    def effect_add_to_faction(target: str) -> str:
        return f"add_to_faction = {require_country_tag(target)}"

    @classmethod
    def effect_add_target_to_faction(cls, faction_leader: str, target: str) -> str:
        """Add ``target`` to ``faction_leader``'s faction with explicit scope."""
        faction_leader = require_country_tag(faction_leader)
        target = require_country_tag(target)
        return cls.scope_block(faction_leader, f"add_to_faction = {target}")

    @classmethod
    def effect_join_faction(cls, actor: str, faction_leader: str) -> str:
        """Make ``actor`` join ``faction_leader``'s faction with explicit direction."""
        return cls.effect_add_target_to_faction(faction_leader, actor)

    @staticmethod
    def effect_white_peace(target: str = "all") -> str:
        return f"white_peace = {'all' if target == 'all' else require_country_tag(target)}"

    @staticmethod
    def effect_release(tag: str) -> str:
        return f"release = {require_country_tag(tag)}"

    @staticmethod
    def effect_release_puppet(tag: str) -> str:
        return f"release_puppet = {require_country_tag(tag)}"

    @classmethod
    def effect_end_puppet(cls, puppet: str, overlord: str | None = None) -> str:
        effect = f"end_puppet = {require_country_tag(puppet)}"
        return cls.scope_block(require_country_tag(overlord), effect) if overlord else effect

    @staticmethod
    def effect_set_autonomy(
        target: str,
        autonomy_state: str,
        *,
        freedom_level: float | int | None = None,
    ) -> str:
        fields: dict[str, object] = {
            "target": require_country_tag(target),
            "autonomy_state": require_script_id(autonomy_state, label="autonomy state"),
        }
        if freedom_level is not None:
            fields["freedom_level"] = freedom_level
        return effect_block("set_autonomy", fields)

    @classmethod
    def effect_convert_puppet_to_ally(
        cls,
        puppet: str,
        overlord: str,
        *,
        faction_leader: str | None = None,
    ) -> str:
        effects = [cls.effect_end_puppet(puppet, overlord)]
        if faction_leader:
            effects.append(cls.effect_join_faction(puppet, faction_leader))
        return "\n".join(effects)

    @staticmethod
    def effect_set_rule(rule: str, value: bool | str = True) -> str:
        return effect_block("set_rule", {require_script_id(rule, label="rule ID"): value})

    @staticmethod
    def effect_schedule_country_event(
        event_id: str, days: int = 0, target: str | None = None
    ) -> str:
        fields: dict[str, object] = {"id": require_script_id(event_id, label="event ID")}
        if days:
            fields["days"] = days
        effect = effect_block("country_event", fields)
        return scope_block(require_country_tag(target), effect) if target else effect

    @staticmethod
    def effect_division_template(
        name: str,
        regiments: str,
        support: str = "",
        division_names_group: str = "",
    ) -> str:
        regiments = normalize_block_body(regiments)
        support = normalize_block_body(support)
        # A stray brace would end a block of the template early.
        for label, body in (("regiments", regiments), ("support", support)):
            require_well_formed(body, f"the {label} of a division template")
        fields = [
            f"name = {pdx_string(name)}",
            f"regiments = {braced(regiments)}",
        ]
        if support:
            fields.append(f"support = {braced(support)}")
        if division_names_group:
            fields.append(f"division_names_group = {pdx_string(division_names_group)}")
        return f"division_template = {{ {' '.join(fields)} }}"

    @staticmethod
    def effect_create_unit(
        division: str,
        owner: str | None = None,
        start_experience_factor: float | None = None,
    ) -> str:
        fields = [f"division = {pdx_string(division)}"]
        if owner:
            fields.append(f"owner = {require_country_tag(owner)}")
        if start_experience_factor is not None:
            fields.append(f"start_experience_factor = {start_experience_factor}")
        return f"create_unit = {{ {' '.join(fields)} }}"

    @classmethod
    def _effect_revolt_payload(
        cls,
        tag: str,
        *,
        manpower: int,
        equipment: dict[str, int] | None,
        technologies: dict[str, int] | None,
        division_template: str,
        units: list[str] | None,
    ) -> list[str]:
        scoped: list[str] = []
        if manpower:
            scoped.append(cls.effect_add_manpower(manpower))
        if technologies:
            scoped.append(cls.effect_set_technologies(technologies))
        for equipment_type, amount in (equipment or {}).items():
            scoped.append(cls.effect_add_equipment(equipment_type, amount))
        if division_template:
            scoped.append(division_template)
        for unit in units or []:
            scoped.append(cls.effect_create_unit(unit))
        return [cls.scope_block(tag, "\n".join(scoped))] if scoped else []

    @classmethod
    def effect_spawn_revolution(
        cls,
        tag: str,
        state_ids: list[int],
        *,
        overlord: str | None = None,
        manpower: int = 0,
        equipment: dict[str, int] | None = None,
        technologies: dict[str, int] | None = None,
        division_template: str = "",
        units: list[str] | None = None,
        faction_leader: str | None = None,
        war_goal_type: str = "annex_everything",
    ) -> str:
        tag = require_country_tag(tag)
        if not state_ids:
            raise ValueError("effect_spawn_revolution() requires at least one state ID")
        if not any([manpower, equipment, technologies, division_template, units]):
            warnings.warn(
                "effect_spawn_revolution() called without manpower, equipment, technologies, templates, or units; "
                "the revolt may spawn as an unplayable shell.",
                RuntimeWarning,
                stacklevel=2,
            )
        effects: list[str] = []
        for state_id in state_ids:
            effects.append(cls.effect_transfer_state_with_core(state_id, tag))
        effects.extend(
            cls._effect_revolt_payload(
                tag,
                manpower=manpower,
                equipment=equipment,
                technologies=technologies,
                division_template=division_template,
                units=units,
            )
        )
        if faction_leader:
            effects.append(cls.effect_join_faction(tag, faction_leader))
        if overlord:
            effects.append(cls.effect_declare_war_from(tag, overlord, war_goal_type))
        return "\n".join(effects)

    @classmethod
    def effect_convert_existing_or_spawn_revolt(
        cls,
        tag: str,
        state_ids: list[int],
        *,
        overlord: str | None = None,
        manpower: int = 0,
        equipment: dict[str, int] | None = None,
        technologies: dict[str, int] | None = None,
        division_template: str = "",
        units: list[str] | None = None,
        faction_leader: str | None = None,
        war_goal_type: str = "annex_everything",
    ) -> str:
        tag = require_country_tag(tag)
        if not state_ids:
            raise ValueError(
                "effect_convert_existing_or_spawn_revolt() requires at least one state ID"
            )
        existing_effects: list[str] = []
        if overlord:
            existing_effects.append(cls.effect_end_puppet(tag, overlord))
        for state_id in state_ids:
            existing_effects.append(cls.effect_transfer_state_with_core(state_id, tag))
        existing_effects.extend(
            cls._effect_revolt_payload(
                tag,
                manpower=manpower,
                equipment=equipment,
                technologies=technologies,
                division_template=division_template,
                units=units,
            )
        )
        if faction_leader:
            existing_effects.append(cls.effect_join_faction(tag, faction_leader))
        if overlord:
            existing_effects.append(cls.effect_declare_war_from(tag, overlord, war_goal_type))

        spawned = cls.effect_spawn_revolution(
            tag,
            state_ids,
            overlord=overlord,
            manpower=manpower,
            equipment=equipment,
            technologies=technologies,
            division_template=division_template,
            units=units,
            faction_leader=faction_leader,
            war_goal_type=war_goal_type,
        )
        return "\n".join(
            [
                f"if = {{ limit = {{ {tag} = {{ exists = yes }} }} {normalize_block_body(_join_script_parts(existing_effects))} }}",
                f"else = {{ {normalize_block_body(spawned)} }}",
            ]
        )

    def create_wargoal(self, target: str, war_goal_type: str = "annex_everything") -> str:
        warnings.warn(
            "create_wargoal() only builds script; use effect_create_wargoal()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.effect_create_wargoal(target, war_goal_type)

    def declare_war(self, target: str, war_goal_type: str = "annex_everything") -> str:
        warnings.warn(
            "declare_war() only builds script; use effect_declare_war()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.effect_declare_war(target, war_goal_type)

    def start_civil_war(self, ideology: str, size: float = 0.5, capital: int | None = None) -> str:
        warnings.warn(
            "start_civil_war() only builds script; use effect_start_civil_war()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.effect_start_civil_war(ideology, size, capital)
