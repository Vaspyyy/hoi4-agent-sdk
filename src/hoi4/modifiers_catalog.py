"""
HOI4 Modding Studio - Idea Modifiers Catalog

Categorized list of modifiers for ideas / national spirits.
"""

from __future__ import annotations


MODIFIER_CATEGORIES: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "Economy",
        [
            ("Industrial Capacity Factory", "industrial_capacity_factory", "0.05"),
            ("Consumer Goods Factor", "consumer_goods_factor", "-0.05"),
            (
                "Production Speed (Infantry Equipment)",
                "production_speed_infantry_equipment_factor",
                "0.10",
            ),
            (
                "Production Speed (Support Equipment)",
                "production_speed_support_equipment_factor",
                "0.10",
            ),
            (
                "Production Speed (Artillery)",
                "production_speed_artillery_factor",
                "0.10",
            ),
            (
                "Production Speed (Light Armor)",
                "production_speed_light_armor_factor",
                "0.10",
            ),
            (
                "Production Speed (Heavy Armor)",
                "production_speed_heavy_armor_factor",
                "0.10",
            ),
            (
                "Production Speed (Naval)",
                "production_speed_naval_equipment_factor",
                "0.10",
            ),
            ("Production Speed (Air)", "production_speed_air_equipment_factor", "0.10"),
            ("Production Speed (All)", "production_speed_factor", "0.10"),
            ("Construction Speed", "construction_speed", "0.10"),
            ("Repair Speed", "repair_speed", "0.10"),
            (
                "Line Artillery Battalion Cost",
                "line_artillery_battalion_cost_factor",
                "-0.05",
            ),
        ],
    ),
    (
        "Politics",
        [
            ("Political Power Gain", "political_power_gain", "0.25"),
            ("Political Power Gain Factor", "political_power_gain_factor", "0.10"),
            ("Stability Factor", "stability_factor", "0.10"),
            ("War Support Factor", "war_support_factor", "0.10"),
            ("Justify War Goal Time", "justify_war_goal_time", "-0.10"),
            ("Autonomy Gain Factor", "autonomy_gain_factor", "0.10"),
        ],
    ),
    (
        "Army",
        [
            ("Army Attack Factor", "army_attack_factor", "0.05"),
            ("Army Defense Factor", "army_defence_factor", "0.05"),
            ("Army Morale Factor", "army_morale_factor", "0.05"),
            ("Land Fortification Max Level", "land_fortification_max_level", "1"),
            ("Army Speed Factor", "army_speed_factor", "0.05"),
            ("Planning Speed", "planning_speed", "0.25"),
            ("Max Planning", "max_planning", "0.10"),
            ("Experience Army Decay Factor", "experience_army_decay_factor", "-0.10"),
            ("Soft Attack Factor", "soft_attack_factor", "0.05"),
            ("Hard Attack Factor", "hard_attack_factor", "0.05"),
            ("Infantry Equipment", "infantry_equipment", "0.05"),
            ("Special Forces Cap", "special_forces_cap", "0.05"),
        ],
    ),
    (
        "Navy",
        [
            ("Navy Attack Factor", "navy_attack_factor", "0.05"),
            ("Navy Defense Factor", "navy_defence_factor", "0.05"),
            ("Naval Hit Chance Factor", "naval_hit_chance_factor", "0.05"),
            ("Screening Efficiency Bonus", "screening_efficiency_bonus", "0.05"),
            ("Submarine Attack Factor", "submarine_attack_factor", "0.05"),
            ("Submarine Visibility Factor", "submarine_visibility_factor", "-0.10"),
            ("Naval Speed Factor", "naval_speed_factor", "0.05"),
            ("Experience Navy Decay Factor", "experience_navy_decay_factor", "-0.10"),
            ("Dockyard Output", "dockyard_output", "0.05"),
        ],
    ),
    (
        "Air",
        [
            ("Air Attack Factor", "air_attack_factor", "0.05"),
            ("Air Defense Factor", "air_defence_factor", "0.05"),
            ("Air Agility Factor", "air_agility_factor", "0.05"),
            ("Air Speed Factor", "air_speed_factor", "0.05"),
            ("Air Bomber Defense Factor", "air_bomber_defence_factor", "0.05"),
            (
                "Air Strategic Bomber Bombing Factor",
                "air_strategic_bomber_bombing_factor",
                "0.05",
            ),
            ("Experience Air Decay Factor", "experience_air_decay_factor", "-0.10"),
        ],
    ),
    (
        "Intelligence",
        [
            ("Encryption", "encryption", "1"),
            ("Decryption", "decryption", "1"),
            ("Intel to Power Factor", "intel_to_power_factor", "0.10"),
            ("Spy Upkeep Cost Factor", "spy_upkeep_cost_factor", "-0.10"),
            ("Spy Operation Cost Factor", "spy_operation_cost_factor", "-0.10"),
        ],
    ),
    (
        "Manpower & Occupation",
        [
            ("Manpower Gain Factor", "manpower_gain_factor", "0.10"),
            ("Monthly Population", "monthly_population", "0.10"),
            ("Resistance Growth Speed", "resistance_growth_speed", "-0.10"),
            ("Compliance Growth Speed", "compliance_growth_speed", "0.10"),
            ("Non Core Manpower", "non_core_manpower", "0.05"),
            ("Garrison Cost Factor", "garrison_cost_factor", "-0.10"),
        ],
    ),
    (
        "Research",
        [
            ("Research Time Factor", "research_time_factor", "-0.05"),
            ("Research Time (Infantry)", "research_time_infantry_factor", "-0.10"),
            ("Research Time (Armor)", "research_time_armor_factor", "-0.10"),
            ("Research Time (Naval)", "research_time_naval_factor", "-0.10"),
            ("Research Time (Air)", "research_time_air_factor", "-0.10"),
            ("Research Time (Industry)", "research_time_industry_factor", "-0.10"),
            (
                "Research Time (Electronics)",
                "research_time_electronics_factor",
                "-0.10",
            ),
        ],
    ),
]
