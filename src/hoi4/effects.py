"""Builders that return ready-made effect script.

Every function returns a string of Paradox script; none of them reads or
changes a mod. Use them for the effect fields of events, decisions, focuses
and ideas::

    from hoi4 import effects

    reward = "\n".join([
        effects.add_political_power(100),
        effects.transfer_state_with_core(42, "WST"),
    ])
    mod.update_focus("WST_focus", "WST_unite", completion_reward=reward)

``block`` and ``scope_block`` build any other effect: ``block("set_politics",
{"ruling_party": "fascism"})`` and ``scope_block("GER", "add_stability = 0.1")``.
Every value and identifier is checked, so text never escapes its block.
"""

from __future__ import annotations

import warnings
from itertools import pairwise
from collections.abc import Mapping, Sequence

from .effects_catalog import TECHNOLOGY_CATEGORIES
from .paths import require_country_tag, require_script_id
from .script import braced, normalize_block_body, pdx_string, pdx_value
from .syntax import quote, require_key, require_well_formed

__all__ = [
    "add_air_experience",
    "add_army_experience",
    "add_building",
    "add_bunker",
    "add_civilian_factory",
    "add_coastal_bunker",
    "add_dynamic_modifier",
    "add_equipment",
    "add_industry_bonus",
    "add_infrastructure",
    "add_manpower",
    "add_military_factory",
    "add_navy_experience",
    "add_political_power",
    "add_stability",
    "add_state_building",
    "add_state_core",
    "add_target_to_faction",
    "add_tech_bonus",
    "add_timed_idea",
    "add_to_faction",
    "add_war_support",
    "block",
    "convert_existing_or_spawn_revolt",
    "convert_puppet_to_ally",
    "create_faction",
    "create_unit",
    "create_wargoal",
    "declare_war",
    "declare_war_from",
    "division_template",
    "end_puppet",
    "force_update_dynamic_modifier",
    "join_faction",
    "load_focus_tree",
    "release",
    "release_puppet",
    "remove_dynamic_modifier",
    "remove_state_core",
    "schedule_country_event",
    "scope_block",
    "set_autonomy",
    "set_politics",
    "set_rule",
    "set_technologies",
    "set_technology",
    "spawn_civil_war_with_focus_tree",
    "spawn_revolution",
    "start_civil_war",
    "swap_idea",
    "transfer_state",
    "transfer_state_with_core",
    "upgrade_idea_chain",
    "white_peace",
]


def _join_script_parts(parts: Sequence[str | None]) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def block(name: str, fields: Mapping[str, object] | None = None, **kwargs: object) -> str:
    """Build ``name = { key = value ... }`` without f-string brace escaping.

    The name and every key have to be one word; a value is written as one
    scalar. Anything else would not stay inside the block.
    """
    data: dict[str, object] = {}
    if fields:
        data.update(fields)
    data.update(kwargs)
    name = require_key(name, "the name of an effect block")
    body = " ".join(
        f"{require_key(key, f'a field of {name}')} = {pdx_value(value)}"
        for key, value in data.items()
    )
    return f"{name} = {{ {body} }}"


def scope_block(scope: str | int, *effects: str) -> str:
    """Build ``scope = { effect... }`` without f-string brace escaping.

    The scope has to be one word and every effect well formed on its own, so
    that nothing lands outside the block.
    """
    scope = require_key(str(scope).strip(), "a scope")
    parts = [effect.strip() for effect in effects if effect and effect.strip()]
    pieces: list[str] = []
    after_comment = False
    for part in parts:
        document = require_well_formed(part, f"an effect in the scope '{scope}'")
        # A comment runs to the end of its line: what follows goes below it.
        if pieces:
            pieces.append("\n" if after_comment else " ")
        pieces.append(part)
        # Each part is well formed on its own, so the body built so far ends
        # in a comment exactly when its last part does: no need to read the
        # whole body again for every part.
        comments = document.comments
        after_comment = bool(comments) and comments[-1][1] == len(part)
    closing = "\n}" if after_comment else " }"
    return f"{scope} = {{ {''.join(pieces)}{closing}"


def add_building(state_id: int, building_type: str, level: int = 1) -> str:
    """Return a state-scoped effect that adds a shared building slot and builds ``building_type``."""
    return (
        f"{state_id} = {{ "
        f"add_extra_state_shared_building_slots = {level} "
        "add_building_construction = { "
        f"type = {quote(building_type)} level = {level} instant_build = yes "
        "} "
        "}"
    )


def add_state_building(
    state_id: int,
    building_type: str,
    level: int = 1,
    *,
    add_slots: int = 0,
    province: int | None = None,
) -> str:
    """Return a state-scoped ``add_building_construction`` effect, optionally adding slots first."""
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
    effects.append(block("add_building_construction", fields))
    return scope_block(state_id, *effects)


def add_civilian_factory(state_id: int, level: int = 1) -> str:
    """Return a state-scoped effect that adds a slot and builds civilian factories."""
    return add_building(state_id, "industrial_complex", level)


def add_military_factory(state_id: int, level: int = 1) -> str:
    """Return a state-scoped effect that adds a slot and builds military factories."""
    return add_building(state_id, "arms_factory", level)


def add_infrastructure(state_id: int, level: int = 1) -> str:
    """Return a state-scoped effect that builds infrastructure."""
    return (
        f"{state_id} = {{ "
        "add_building_construction = { "
        f"type = infrastructure level = {level} instant_build = yes "
        "} "
        "}"
    )


def add_bunker(state_id: int, level: int = 1, province: int | None = None) -> str:
    """Return a state-scoped effect that builds land forts, optionally in one province."""
    return add_state_building(state_id, "bunker", level, province=province)


def add_coastal_bunker(state_id: int, level: int = 1, province: int | None = None) -> str:
    """Return a state-scoped effect that builds coastal forts, optionally in one province."""
    return add_state_building(state_id, "coastal_bunker", level, province=province)


def add_tech_bonus(
    name: str,
    category: str = "industry",
    uses: int = 1,
    bonus: float = 0.5,
) -> str:
    """Return ``add_tech_bonus = { ... }`` for one research category."""
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


def add_industry_bonus(name: str, uses: int = 1, bonus: float = 0.5) -> str:
    """Return ``add_tech_bonus = { ... }`` for the industry category."""
    return add_tech_bonus(name, category="industry", uses=uses, bonus=bonus)


def add_state_core(state_id: int, tag: str) -> str:
    """Return ``<state> = { add_core_of = <tag> }``."""
    return f"{state_id} = {{ add_core_of = {require_country_tag(tag)} }}"


def remove_state_core(state_id: int, tag: str) -> str:
    """Return ``<state> = { remove_core_of = <tag> }``."""
    return f"{state_id} = {{ remove_core_of = {require_country_tag(tag)} }}"


def transfer_state(state_id: int, target: str) -> str:
    """Return ``<target> = { transfer_state = <state> }``."""
    return f"{require_country_tag(target)} = {{ transfer_state = {state_id} }}"


def transfer_state_with_core(state_id: int, target: str) -> str:
    """Return a state transfer to ``target`` that also adds its core."""
    return "\n".join(
        [
            transfer_state(state_id, target),
            add_state_core(state_id, target),
        ]
    )


def add_political_power(amount: int) -> str:
    """Return ``add_political_power = <amount>``."""
    return f"add_political_power = {amount}"


def add_war_support(amount: float) -> str:
    """Return ``add_war_support = <amount>``."""
    return f"add_war_support = {amount}"


def add_stability(amount: float) -> str:
    """Return ``add_stability = <amount>``."""
    return f"add_stability = {amount}"


def add_manpower(amount: int) -> str:
    """Return ``add_manpower = <amount>``."""
    return f"add_manpower = {amount}"


def add_army_experience(amount: int | float) -> str:
    """Return ``army_experience = <amount>``."""
    return f"army_experience = {amount}"


def add_navy_experience(amount: int | float) -> str:
    """Return ``navy_experience = <amount>``."""
    return f"navy_experience = {amount}"


def add_air_experience(amount: int | float) -> str:
    """Return ``air_experience = <amount>``."""
    return f"air_experience = {amount}"


def add_equipment(
    equipment_type: str,
    amount: int,
    producer: str | None = None,
    variant_name: str | None = None,
) -> str:
    """Return ``add_equipment_to_stockpile = { ... }``, optionally with a producer or variant."""
    fields: dict[str, object] = {"type": equipment_type, "amount": amount}
    if producer:
        fields["producer"] = require_country_tag(producer)
    if variant_name:
        fields["variant_name"] = variant_name
    return block("add_equipment_to_stockpile", fields)


def set_technology(technology: str, level: int = 1, popup: bool | None = None) -> str:
    """Return ``set_technology = { <technology> = <level> }``, optionally without popup."""
    technology = require_script_id(technology, label="technology ID")
    fields: dict[str, object] = {technology: level}
    if popup is not None:
        fields["popup"] = popup
    return block("set_technology", fields)


def set_technologies(technologies: dict[str, int]) -> str:
    """Return one ``set_technology = { ... }`` block for several technologies."""
    return block(
        "set_technology",
        {
            require_script_id(technology, label="technology ID"): level
            for technology, level in technologies.items()
        },
    )


def add_timed_idea(idea_id: str, days: int) -> str:
    """Return ``add_timed_idea = { idea = ... days = ... }``."""
    idea_id = require_script_id(idea_id, label="idea ID")
    return f"add_timed_idea = {{ idea = {idea_id} days = {days} }}"


def add_dynamic_modifier(
    modifier_id: str,
    *,
    days: int | str | None = None,
    scope: str | int | None = None,
) -> str:
    """Return ``add_dynamic_modifier = { ... }``, optionally timed.

    ``scope`` is the effect's own ``scope = ...`` field: the country or state
    the modifier's variables are read from. It is not where the effect runs.
    To add a modifier to a state from country scope, wrap the effect:
    ``scope_block(85, add_dynamic_modifier("id"))``.
    """
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
    return block("add_dynamic_modifier", fields)


def remove_dynamic_modifier(
    modifier_id: str,
    *,
    scope: str | int | None = None,
) -> str:
    """Return ``remove_dynamic_modifier = { ... }``..

    ``scope`` is the effect's own ``scope = ...`` field: the country or state
    the modifier's variables are read from. It is not where the effect runs.
    To remove a modifier from a state from country scope, wrap the effect:
    ``scope_block(85, remove_dynamic_modifier("id"))``.
    """
    fields: dict[str, object] = {
        "modifier": require_script_id(
            modifier_id,
            label="dynamic modifier ID",
        )
    }
    if scope is not None:
        fields["scope"] = scope
    return block("remove_dynamic_modifier", fields)


def force_update_dynamic_modifier() -> str:
    """Return ``force_update_dynamic_modifier = yes``."""
    return "force_update_dynamic_modifier = yes"


def swap_idea(old: str, new: str, target: str | None = None) -> str:
    """Return ``swap_ideas = { remove_idea = ... add_idea = ... }``, optionally scoped."""
    old = require_script_id(old, label="idea ID")
    new = require_script_id(new, label="idea ID")
    effect = f"swap_ideas = {{ remove_idea = {old} add_idea = {new} }}"
    return scope_block(require_country_tag(target), effect) if target else effect


def upgrade_idea_chain(ideas: list[str], target: str | None = None) -> str:
    """Return a conditional chain that swaps each staged idea for the next one."""
    if len(ideas) < 2:
        raise ValueError("effects.upgrade_idea_chain() requires at least two idea IDs")
    ideas = [require_script_id(idea, label="idea ID") for idea in ideas]
    effects: list[str] = []
    for old, new in reversed(list(pairwise(ideas))):
        effects.append(
            f"if = {{ limit = {{ has_idea = {old} }} {swap_idea(old, new)} }}"
        )
    missing_checks = " ".join(f"NOT = {{ has_idea = {idea} }}" for idea in ideas)
    effects.append(f"if = {{ limit = {{ {missing_checks} }} add_ideas = {ideas[0]} }}")
    body = "\n".join(effects)
    return scope_block(require_country_tag(target), body) if target else body


def create_wargoal(target: str, war_goal_type: str = "annex_everything") -> str:
    """Return ``create_wargoal = { type = ... target = ... }``."""
    target = require_country_tag(target)
    war_goal_type = require_script_id(war_goal_type, label="war goal type")
    return f"create_wargoal = {{ type = {war_goal_type} target = {target} }}"


def declare_war(target: str, war_goal_type: str = "annex_everything") -> str:
    """Return ``declare_war_on = { type = ... target = ... }``."""
    target = require_country_tag(target)
    war_goal_type = require_script_id(war_goal_type, label="war goal type")
    return f"declare_war_on = {{ type = {war_goal_type} target = {target} }}"


def declare_war_from(
    actor: str,
    target: str,
    war_goal_type: str = "annex_everything",
) -> str:
    """Return ``<actor> = { declare_war_on = { ... } }``."""
    return scope_block(
        require_country_tag(actor), declare_war(target, war_goal_type)
    )


def start_civil_war(
    ideology: str,
    size: float = 0.5,
    capital: int | None = None,
    *,
    effects: Sequence[str] | None = None,
) -> str:
    """Return ``start_civil_war = { ideology = ... size = ... }`` with optional effects."""
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


def load_focus_tree(
    tree_id: str,
    *,
    keep_completed: bool = False,
    copy_completed_from: str | None = None,
    mark_layout_dirty: bool = True,
) -> str:
    """Return ``load_focus_tree = { ... }``, optionally marking the layout dirty."""
    tree_id = require_script_id(tree_id, label="focus tree ID")
    fields: dict[str, object] = {"tree": tree_id, "keep_completed": keep_completed}
    if copy_completed_from:
        fields["copy_completed_from"] = require_country_tag(copy_completed_from)
    effects = [block("load_focus_tree", fields)]
    if mark_layout_dirty:
        effects.append("mark_focus_tree_layout_dirty = yes")
    return "\n".join(effects)


def spawn_civil_war_with_focus_tree(
    ideology: str,
    tree_id: str,
    *,
    size: float = 0.5,
    capital: int | None = None,
    keep_completed: bool = False,
    copy_completed_from: str | None = None,
    mark_layout_dirty: bool = True,
) -> str:
    """Return a civil war whose new country loads ``tree_id``."""
    load_tree = load_focus_tree(
        tree_id,
        keep_completed=keep_completed,
        copy_completed_from=copy_completed_from,
        mark_layout_dirty=mark_layout_dirty,
    )
    return start_civil_war(
        ideology,
        size=size,
        capital=capital,
        effects=[load_tree],
    )


def set_politics(
    ruling_party: str,
    *,
    elections_allowed: bool | None = None,
) -> str:
    """Return ``set_politics = { ruling_party = ... }``."""
    ruling_party = require_script_id(ruling_party, label="ruling party")
    fields: dict[str, object] = {"ruling_party": ruling_party}
    if elections_allowed is not None:
        fields["elections_allowed"] = elections_allowed
    return block("set_politics", fields)


def create_faction(name: str) -> str:
    """Return ``create_faction = "<name>"``."""
    return f"create_faction = {pdx_string(name)}"


def add_to_faction(target: str) -> str:
    """Return a bare ``add_to_faction = <target>`` for the current scope."""
    return f"add_to_faction = {require_country_tag(target)}"


def add_target_to_faction(faction_leader: str, target: str) -> str:
    """Add ``target`` to ``faction_leader``'s faction with explicit scope."""
    faction_leader = require_country_tag(faction_leader)
    target = require_country_tag(target)
    return scope_block(faction_leader, f"add_to_faction = {target}")


def join_faction(actor: str, faction_leader: str) -> str:
    """Make ``actor`` join ``faction_leader``'s faction with explicit direction."""
    return add_target_to_faction(faction_leader, actor)


def white_peace(target: str = "all") -> str:
    """Return ``white_peace = <target>``."""
    return f"white_peace = {'all' if target == 'all' else require_country_tag(target)}"


def release(tag: str) -> str:
    """Return ``release = <tag>``."""
    return f"release = {require_country_tag(tag)}"


def release_puppet(tag: str) -> str:
    """Return ``release_puppet = <tag>``."""
    return f"release_puppet = {require_country_tag(tag)}"


def end_puppet(puppet: str, overlord: str | None = None) -> str:
    """Return ``end_puppet = <puppet>``, scoped to the overlord when given."""
    effect = f"end_puppet = {require_country_tag(puppet)}"
    return scope_block(require_country_tag(overlord), effect) if overlord else effect


def set_autonomy(
    target: str,
    autonomy_state: str,
    *,
    freedom_level: float | int | None = None,
) -> str:
    """Return ``set_autonomy = { target = ... autonomy_state = ... }``."""
    fields: dict[str, object] = {
        "target": require_country_tag(target),
        "autonomy_state": require_script_id(autonomy_state, label="autonomy state"),
    }
    if freedom_level is not None:
        fields["freedom_level"] = freedom_level
    return block("set_autonomy", fields)


def convert_puppet_to_ally(
    puppet: str,
    overlord: str,
    *,
    faction_leader: str | None = None,
) -> str:
    """Return effects that end a puppet relationship and optionally join a faction."""
    effects = [end_puppet(puppet, overlord)]
    if faction_leader:
        effects.append(join_faction(puppet, faction_leader))
    return "\n".join(effects)


def set_rule(rule: str, value: bool | str = True) -> str:
    """Return ``set_rule = { <rule> = <value> }``."""
    return block("set_rule", {require_script_id(rule, label="rule ID"): value})


def schedule_country_event(
    event_id: str, days: int = 0, target: str | None = None
) -> str:
    """Return ``country_event = { id = ... }``, optionally delayed or scoped."""
    fields: dict[str, object] = {"id": require_script_id(event_id, label="event ID")}
    if days:
        fields["days"] = days
    effect = block("country_event", fields)
    return scope_block(require_country_tag(target), effect) if target else effect


def division_template(
    name: str,
    regiments: str,
    support: str = "",
    division_names_group: str = "",
) -> str:
    """Return a ``division_template = { ... }`` block."""
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


def create_unit(
    division: str,
    owner: str | None = None,
    start_experience_factor: float | None = None,
) -> str:
    """Return ``create_unit = { division = ... }``."""
    fields = [f"division = {pdx_string(division)}"]
    if owner:
        fields.append(f"owner = {require_country_tag(owner)}")
    if start_experience_factor is not None:
        fields.append(f"start_experience_factor = {start_experience_factor}")
    return f"create_unit = {{ {' '.join(fields)} }}"


def _revolt_payload(
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
        scoped.append(add_manpower(manpower))
    if technologies:
        scoped.append(set_technologies(technologies))
    for equipment_type, amount in (equipment or {}).items():
        scoped.append(add_equipment(equipment_type, amount))
    if division_template:
        scoped.append(division_template)
    for unit in units or []:
        scoped.append(create_unit(unit))
    return [scope_block(tag, "\n".join(scoped))] if scoped else []


def spawn_revolution(
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
    """Return effects that set up ``tag`` as a revolt in ``state_ids``."""
    tag = require_country_tag(tag)
    if not state_ids:
        raise ValueError("effects.spawn_revolution() requires at least one state ID")
    if not any([manpower, equipment, technologies, division_template, units]):
        warnings.warn(
            "effects.spawn_revolution() called without manpower, equipment, technologies, templates, or units; "
            "the revolt may spawn as an unplayable shell.",
            RuntimeWarning,
            stacklevel=2,
        )
    effects: list[str] = []
    for state_id in state_ids:
        effects.append(transfer_state_with_core(state_id, tag))
    effects.extend(
        _revolt_payload(
            tag,
            manpower=manpower,
            equipment=equipment,
            technologies=technologies,
            division_template=division_template,
            units=units,
        )
    )
    if faction_leader:
        effects.append(join_faction(tag, faction_leader))
    if overlord:
        effects.append(declare_war_from(tag, overlord, war_goal_type))
    return "\n".join(effects)


def convert_existing_or_spawn_revolt(
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
    """Return effects that use ``tag`` if it exists and otherwise spawn it as a revolt."""
    tag = require_country_tag(tag)
    if not state_ids:
        raise ValueError(
            "effects.convert_existing_or_spawn_revolt() requires at least one state ID"
        )
    existing_effects: list[str] = []
    if overlord:
        existing_effects.append(end_puppet(tag, overlord))
    for state_id in state_ids:
        existing_effects.append(transfer_state_with_core(state_id, tag))
    existing_effects.extend(
        _revolt_payload(
            tag,
            manpower=manpower,
            equipment=equipment,
            technologies=technologies,
            division_template=division_template,
            units=units,
        )
    )
    if faction_leader:
        existing_effects.append(join_faction(tag, faction_leader))
    if overlord:
        existing_effects.append(declare_war_from(tag, overlord, war_goal_type))

    spawned = spawn_revolution(
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
