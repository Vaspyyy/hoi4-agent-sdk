"""Curated HOI4 1.19 idea and national-spirit modifiers."""

from __future__ import annotations


MODIFIER_CATEGORIES: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "Economy",
        [
            ("Industrial Capacity Factory", "industrial_capacity_factory", "0.05"),
            ("Consumer Goods Factor", "consumer_goods_factor", "-0.05"),
            (
                "Building Construction Speed",
                "production_speed_buildings_factor",
                "0.10",
            ),
            (
                "Powered Building Construction Speed",
                "production_speed_buildings_powered_factor",
                "0.10",
            ),
            ("Facility Construction Speed", "production_speed_facility_factor", "0.10"),
            (
                "State Building Construction Speed",
                "state_production_speed_buildings_factor",
                "0.10",
            ),
            (
                "Factory Efficiency Gain",
                "production_factory_efficiency_gain_factor",
                "0.10",
            ),
            ("Industry Repair", "industry_repair_factor", "0.10"),
            ("Repair Speed", "repair_speed_factor", "0.10"),
            ("Industrial Capacity Dockyard", "industrial_capacity_dockyard", "0.05"),
            (
                "Powered Factory Output",
                "industrial_capacity_factory_powered",
                "0.05",
            ),
            (
                "Maximum Factory Efficiency",
                "production_factory_max_efficiency_factor",
                "0.05",
            ),
            (
                "Starting Factory Efficiency",
                "production_factory_start_efficiency_factor",
                "0.05",
            ),
        ],
    ),
    (
        "Politics",
        [
            ("Political Power Gain", "political_power_gain", "0.25"),
            ("Political Power Factor", "political_power_factor", "0.10"),
            ("Stability Factor", "stability_factor", "0.10"),
            ("War Support Factor", "war_support_factor", "0.10"),
            ("Justify War Goal Time", "justify_war_goal_time", "-0.10"),
            ("Global Autonomy Gain", "autonomy_gain_global_factor", "0.10"),
        ],
    ),
    (
        "Army",
        [
            ("Army Attack Factor", "army_attack_factor", "0.05"),
            ("Army Defense Factor", "army_defence_factor", "0.05"),
            ("Army Morale Factor", "army_morale_factor", "0.05"),
            ("Army Speed Factor", "army_speed_factor", "0.05"),
            ("Planning Speed", "planning_speed", "0.25"),
            ("Max Planning", "max_planning", "0.10"),
            ("Special Forces Cap", "special_forces_cap", "0.05"),
            ("Fortification Damage", "fortification_damage", "0.05"),
            ("Army Experience Gain", "experience_gain_army_factor", "0.10"),
            ("Infantry Attack", "army_infantry_attack_factor", "0.05"),
            ("Infantry Defense", "army_infantry_defence_factor", "0.05"),
            ("Equipment Capture", "equipment_capture", "0.05"),
        ],
    ),
    (
        "Navy",
        [
            ("Naval Damage", "naval_damage_factor", "0.05"),
            ("Naval Defense", "naval_defense_factor", "0.05"),
            ("Naval Hit Chance", "naval_hit_chance", "0.05"),
            ("Screening Efficiency", "screening_efficiency", "0.05"),
            ("Submarine Attack", "navy_submarine_attack_factor", "0.05"),
            ("Naval Visibility", "navy_visibility", "-0.10"),
            ("Naval Speed", "naval_speed_factor", "0.05"),
            ("Navy Experience Gain", "experience_gain_navy_factor", "0.10"),
            ("Dockyard Output", "industrial_capacity_dockyard", "0.05"),
        ],
    ),
    (
        "Air",
        [
            ("Air Attack", "air_attack_factor", "0.05"),
            ("Air Defense", "air_defence_factor", "0.05"),
            ("Air Agility", "air_agility_factor", "0.05"),
            ("Maximum Air Speed", "air_maximum_speed_factor", "0.05"),
            (
                "Strategic Bomber Defense",
                "air_strategic_bomber_defence_factor",
                "0.05",
            ),
            (
                "Strategic Bomber Bombing",
                "air_strategic_bomber_bombing_factor",
                "0.05",
            ),
            ("Air Experience Gain", "experience_gain_air_factor", "0.10"),
        ],
    ),
    (
        "Intelligence",
        [
            ("Encryption", "encryption", "1"),
            ("Decryption", "decryption", "1"),
            ("Decryption Power", "decryption_power_factor", "0.10"),
            ("Operation Cost", "operation_cost", "-0.10"),
            ("Intel Network Gain", "intel_network_gain_factor", "0.10"),
        ],
    ),
    (
        "Manpower & Occupation",
        [
            ("Monthly Population", "monthly_population", "0.10"),
            ("Resistance Growth", "resistance_growth", "-0.10"),
            ("Compliance Growth", "compliance_growth", "0.10"),
            ("Non-Core Manpower", "non_core_manpower", "0.05"),
            ("Required Garrison", "required_garrison_factor", "-0.10"),
            ("Weekly Manpower", "weekly_manpower", "1000"),
        ],
    ),
    (
        "Research",
        [
            ("Research Speed", "research_speed_factor", "0.05"),
            (
                "Research Sharing Bonus",
                "research_sharing_per_country_bonus_factor",
                "0.05",
            ),
            ("Scientist Research Bonus", "scientist_research_bonus_factor", "0.05"),
            (
                "MIO Research Bonus",
                "military_industrial_organization_research_bonus",
                "0.05",
            ),
        ],
    ),
]
