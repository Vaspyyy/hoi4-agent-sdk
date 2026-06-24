"""
HOI4 Modding Studio - Event Effects Catalog

Categorized list of event effects for the event builder and focus tree editor.
"""

from __future__ import annotations


EFFECT_CATEGORIES: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Political",
        [
            ("Political Power (+100)", "add_political_power = 100"),
            ("Political Power (+200)", "add_political_power = 200"),
            ("Stability (+5%)", "add_stability = 0.05"),
            ("Stability (+10%)", "add_stability = 0.10"),
            ("War Support (+5%)", "add_war_support = 0.05"),
            ("War Support (+10%)", "add_war_support = 0.10"),
            ("National Unity (+10%)", "add_national_unity = 0.1"),
        ],
    ),
    (
        "Ideology",
        [
            (
                "Add Popularity (democratic)",
                "add_popularity = { ideology = democratic popularity = 0.1 }",
            ),
            (
                "Add Popularity (fascism)",
                "add_popularity = { ideology = fascism popularity = 0.1 }",
            ),
            (
                "Add Popularity (communism)",
                "add_popularity = { ideology = communism popularity = 0.1 }",
            ),
            (
                "Add Popularity (neutrality)",
                "add_popularity = { ideology = neutrality popularity = 0.1 }",
            ),
            (
                "Set Ruling Party (democratic)",
                "set_politics = { ruling_party = democratic }",
            ),
            ("Set Ruling Party (fascism)", "set_politics = { ruling_party = fascism }"),
            (
                "Set Ruling Party (communism)",
                "set_politics = { ruling_party = communism }",
            ),
            (
                "Set Ruling Party (neutrality)",
                "set_politics = { ruling_party = neutrality }",
            ),
        ],
    ),
    (
        "Economy",
        [
            (
                "Civilian Factory (+1)",
                "add_building_construction = { type = industrial_complex level = 1 instant_build = yes }",
            ),
            (
                "Military Factory (+1)",
                "add_building_construction = { type = arms_factory level = 1 instant_build = yes }",
            ),
            (
                "Infrastructure (+1)",
                "add_building_construction = { type = infrastructure level = 1 instant_build = yes }",
            ),
            (
                "Synthetic Refinery (+1)",
                "add_building_construction = { type = synthetic_refinery level = 1 instant_build = yes }",
            ),
            (
                "Nuclear Reactor (+1)",
                "add_building_construction = { type = nuclear_reactor level = 1 instant_build = yes }",
            ),
            (
                "Radar Station (+1)",
                "add_building_construction = { type = radar_station level = 1 instant_build = yes }",
            ),
            (
                "Bunker (+1)",
                "add_building_construction = { type = bunker level = 1 instant_build = yes }",
            ),
            (
                "Coastal Fortress (+1)",
                "add_building_construction = { type = coastal_bunker level = 1 instant_build = yes }",
            ),
        ],
    ),
    (
        "Military",
        [
            ("Army Experience (+10)", "add_army_experience = 10"),
            ("Navy Experience (+10)", "add_navy_experience = 10"),
            ("Air Experience (+10)", "add_air_experience = 10"),
            ("Command Power (+25)", "add_command_power = 25"),
            ("Manpower (+50k)", "add_manpower = 50000"),
            ("Manpower (+100k)", "add_manpower = 100000"),
            ("Fuel (+500)", "add_fuel = 500"),
            (
                "Add Equipment",
                "add_equipment_to_stockpile = { type = infantry_equipment_0 count = 1000 }",
            ),
        ],
    ),
    (
        "Diplomacy",
        [
            ("Create Faction", 'create_faction = "My Faction"'),
            ("Join Faction", "country_event = { id = generic.5 }"),
            ("Leave Faction", "leave_faction = yes"),
            ("White Peace (all)", "white_peace = all"),
            (
                "Add Opinion (+)",
                "add_opinion_modifier = { target = FROM modifier = positive_relation }",
            ),
            (
                "Add Opinion (-)",
                "add_opinion_modifier = { target = FROM modifier = negative_relation }",
            ),
        ],
    ),
    (
        "Territory",
        [
            ("Transfer State", "transfer_state = 123"),
            ("Add Core", "add_core_of = FROM"),
            ("Remove Core", "remove_core_of = FROM"),
            (
                "Annex Country",
                "annex_country = { target = FROM transfer_troops = yes }",
            ),
            ("Puppet Country", "puppet = FROM"),
            ("Release Puppet", "release_puppet = FROM"),
            ("Set Capital", "set_capital = 123"),
        ],
    ),
    (
        "War Goals",
        [
            (
                "Wargoal (Annex)",
                "create_wargoal = { type = take_state_focus target = ROOT generator = { 123 } }",
            ),
            (
                "Wargoal (Puppet)",
                "create_wargoal = { type = puppet_wargoal_focus target = FROM generator = { all } }",
            ),
            (
                "Wargoal (Liberate)",
                "create_wargoal = { type = liberate_wargoal target = FROM generator = { all } }",
            ),
            (
                "Wargoal (Force Ideology)",
                "create_wargoal = { type = force_government_type target = FROM government_type = democratic }",
            ),
            (
                "Wargoal (Take Claims)",
                "create_wargoal = { type = take_claimed_state target = FROM generator = { all } }",
            ),
        ],
    ),
    (
        "Technology",
        [
            ("Research Slot (+1)", "add_research_slot = 1"),
            (
                "Free Tech Bonus",
                "add_tech_bonus = { bonus = 1.0 category = infantry_weapons }",
            ),
        ],
    ),
    (
        "Custom",
        [
            ("Custom Effect", "# Add your custom effect here"),
        ],
    ),
]
